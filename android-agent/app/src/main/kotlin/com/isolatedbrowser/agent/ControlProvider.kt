package com.isolatedbrowser.agent

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.net.Uri
import android.net.VpnService
import android.os.Binder
import android.os.Bundle
import android.util.Base64
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetAddress
import java.net.URL

/**
 * Control channel for the worker: `adb shell content call --user N --uri content://AUTHORITY --method M`.
 * Only ADB's shell user (and root) may call it, so other apps cannot read status or change the proxy.
 * Every reply is one JSON object in the "json" key.
 */
class ControlProvider : ContentProvider() {

    override fun call(method: String, arg: String?, extras: Bundle?): Bundle {
        val caller = Binder.getCallingUid() % 100000
        if (caller != SHELL_UID && caller != ROOT_UID) {
            return reply(JSONObject().put("ok", false).put("error", "caller not allowed"))
        }
        val result = runCatching {
            when (method) {
                "status" -> status()
                "set_config" -> setConfig(arg)
                "stop" -> { service()?.shutdown(); JSONObject().put("ok", true) }
                "check" -> check()
                "set_account" -> { AccountStore.saveDetails(context!!, decode(arg)); JSONObject().put("ok", true) }
                "same_proxy" -> JSONObject().put("ok", true).put("same", ProxyConfig.load(context!!) == config(arg))
                "try_config" -> tryConfig(arg)
                "export" -> export(arg)
                "set_pin" -> changePin(arg, clear = false)
                "clear_pin" -> changePin(arg, clear = true)
                else -> JSONObject().put("ok", false).put("error", "unknown method")
            }
        }.getOrElse { JSONObject().put("ok", false).put("error", it.message ?: it.javaClass.simpleName) }
        return reply(result)
    }

    private fun service() = AgentVpnService.instance

    private fun status(): JSONObject {
        val json = JSONObject()
            .put("ok", true)
            .put("version", BuildConfig.VERSION_NAME)
            .put("user_id", AgentVpnService.userId())
            .put("vpn_permission", VpnService.prepare(context) == null)
            .put("service_running", service() != null)
            .put("proxy_configured", ProxyConfig.load(context!!) != null)
        // The saved proxy is reported even while the VPN is stopped, without its password.
        ProxyConfig.load(context!!)?.let { json.put("saved_proxy", "${it.host}:${it.port}").put("saved_username", it.username) }
        json.put("account", AccountStore.details(context!!))
        service()?.status()?.forEach { (key, value) -> json.put(key, value ?: JSONObject.NULL) }
        return json
    }

    /** Every argument is base64 JSON, so no shell quoting is needed. */
    private fun decode(arg: String?): JSONObject =
        JSONObject(String(Base64.decode(requireNotNull(arg) { "argument is required" }, Base64.DEFAULT)))

    /** arg is {host, port, username, password}. */
    private fun config(arg: String?) = ProxyConfig.fromJson(decode(arg))

    private fun setConfig(arg: String?): JSONObject {
        val config = config(arg)
        val running = service()
        if (running != null) running.applyConfig(config) else ProxyConfig.save(context!!, config)
        return JSONObject().put("ok", true).put("applied", running != null)
    }

    /**
     * Applies a proxy and keeps it only if a reply comes back through it; otherwise the phone goes
     * back to its old proxy. The probe uses a literal address, because DNS can take about 30 s to
     * recover after the engine restarts.
     */
    private fun tryConfig(arg: String?): JSONObject {
        val candidate = config(arg)
        val running = service() ?: return JSONObject().put("ok", false).put("error", "The VPN is not running in this Android user.")
        val previous = ProxyConfig.load(context!!)
        running.applyConfig(candidate)
        val deadline = System.currentTimeMillis() + TRY_TIMEOUT_MS
        while (System.currentTimeMillis() < deadline) {
            val trace = runCatching { traceIp("1.1.1.1") }.getOrNull()
            if (!trace?.optString("ip").isNullOrEmpty()) return JSONObject().put("ok", true).put("applied", true).put("exit", trace)
            Thread.sleep(TRY_RETRY_MS)
        }
        previous?.let { running.applyConfig(it) }
        return JSONObject().put("ok", true).put("applied", false).put("reverted", previous != null)
    }

    /** Account details and the proxy with its password, for a PC taking this phone over. */
    private fun export(arg: String?): JSONObject {
        val pin = arg?.let { decode(it).optString("pin").ifEmpty { null } }
        if (!AccountStore.pinMatches(context!!, pin)) return JSONObject().put("ok", false).put("error", "wrong_pin")
        return JSONObject().put("ok", true).put("account", AccountStore.details(context!!))
            .put("proxy", ProxyConfig.load(context!!)?.toJson() ?: JSONObject.NULL)
    }

    /**
     * arg is {pin?, current_pin?, proxy?}. With a PIN set, the current PIN or the saved proxy's full
     * details are needed: the PIN only protects the proxy, so knowing the proxy is as good.
     */
    private fun changePin(arg: String?, clear: Boolean): JSONObject {
        val request = decode(arg)
        val context = context!!
        if (AccountStore.pinSet(context)) {
            val byPin = AccountStore.pinMatches(context, request.optString("current_pin").ifEmpty { null })
            val byProxy = request.optJSONObject("proxy")?.let { ProxyConfig.fromJson(it) == ProxyConfig.load(context) } == true
            if (!byPin && !byProxy) return JSONObject().put("ok", false).put("error", "wrong_pin")
        }
        if (clear) AccountStore.clearPin(context) else AccountStore.setPin(context, request.getString("pin"))
        return JSONObject().put("ok", true)
    }

    /**
     * Measures from inside this Android user, through the tunnel: this app is not excluded from
     * its own VPN, so its requests take the same path as every other app's.
     */
    private fun check(): JSONObject {
        val json = JSONObject().put("ok", true)
        // Mapped DNS answers with addresses from 100.64.0.0/10; anything else came from outside the tunnel.
        json.put("dns", runCatching {
            val address = InetAddress.getByName("ipwho.is")
            JSONObject().put("address", address.hostAddress).put("via_tunnel", inMappedRange(address))
        }.getOrElse { JSONObject().put("error", it.message ?: "lookup failed") })
        json.put("exit", runCatching { exitIp() }.getOrElse { JSONObject().put("error", it.message ?: "request failed") })
        // Literal addresses skip DNS. A bare TCP connect is not evidence: the engine accepts
        // connections locally before reaching the proxy, so only a completed HTTPS reply counts.
        json.put("trace_ipv4", runCatching { traceIp("1.1.1.1") }.getOrElse { JSONObject().put("error", it.message ?: "request failed") })
        json.put("trace_ipv6", runCatching { traceIp("[2606:4700:4700::1111]") }.getOrElse { JSONObject().put("error", it.message ?: "request failed") })
        return json
    }

    private fun exitIp(): JSONObject {
        val connection = URL("https://ipwho.is/").openConnection() as HttpURLConnection
        connection.connectTimeout = 10000
        connection.readTimeout = 10000
        try {
            val body = JSONObject(connection.inputStream.bufferedReader().readText())
            return JSONObject().put("ip", body.optString("ip")).put("country", body.optString("country"))
                .put("timezone", body.optJSONObject("timezone")?.optString("id"))
        } finally {
            connection.disconnect()
        }
    }

    /** Cloudflare's trace page reports the address the request arrived from. */
    private fun traceIp(host: String): JSONObject {
        val connection = URL("https://$host/cdn-cgi/trace").openConnection() as HttpURLConnection
        connection.connectTimeout = 10000
        connection.readTimeout = 10000
        try {
            val fields = connection.inputStream.bufferedReader().readLines()
                .mapNotNull { line -> line.split('=', limit = 2).takeIf { it.size == 2 }?.let { it[0] to it[1] } }.toMap()
            return JSONObject().put("ip", fields["ip"]).put("location", fields["loc"])
        } finally {
            connection.disconnect()
        }
    }

    private fun inMappedRange(address: InetAddress): Boolean {
        if (address !is Inet4Address) return false
        val bytes = address.address
        return (bytes[0].toInt() and 0xff) == 100 && (bytes[1].toInt() and 0xc0) == 64
    }

    private fun reply(json: JSONObject) = Bundle().apply { putString("json", json.toString()) }

    private companion object {
        // android.os.Process does not expose these as public API.
        const val ROOT_UID = 0
        const val SHELL_UID = 2000
        const val TRY_TIMEOUT_MS = 30000L
        const val TRY_RETRY_MS = 3000L
    }

    override fun onCreate() = true
    override fun query(uri: Uri, p: Array<out String>?, s: String?, a: Array<out String>?, o: String?): Cursor? = null
    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, s: String?, a: Array<out String>?) = 0
    override fun update(uri: Uri, v: ContentValues?, s: String?, a: Array<out String>?) = 0
}
