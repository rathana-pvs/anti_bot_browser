package com.isolatedbrowser.agent

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.net.InetAddress

/** The SOCKS5 upstream for this Android user. Stored in app-private storage only. */
data class ProxyConfig(val host: String, val port: Int, val username: String, val password: String) {

    init {
        require(port in 1..65535) { "port must be 1-65535" }
        require(isIpLiteral(host)) { "host must be an IP address" }
    }

    fun address(): InetAddress = InetAddress.getByName(host)

    fun toJson(): JSONObject = JSONObject()
        .put("host", host).put("port", port).put("username", username).put("password", password)

    /** hev-socks5-tunnel YAML. DNS is answered by mapped DNS and resolved through the proxy. */
    fun toTunnelYaml(logFile: File, dnsAddress: String): String = buildString {
        appendLine("tunnel:")
        appendLine("  mtu: $MTU")
        appendLine("  ipv4: $TUN_IPV4")
        appendLine("  ipv6: '$TUN_IPV6'")
        appendLine("socks5:")
        appendLine("  address: ${quote(host)}")
        appendLine("  port: $port")
        appendLine("  udp: 'udp'")
        if (username.isNotEmpty()) {
            appendLine("  username: ${quote(username)}")
            appendLine("  password: ${quote(password)}")
        }
        appendLine("mapdns:")
        appendLine("  address: $dnsAddress")
        appendLine("  port: 53")
        appendLine("  network: $MAPPED_NETWORK")
        appendLine("  netmask: $MAPPED_NETMASK")
        appendLine("  cache-size: 10000")
        appendLine("misc:")
        appendLine("  log-file: ${quote(logFile.absolutePath)}")
        appendLine("  log-level: warn")
    }

    companion object {
        const val MTU = 8500
        const val TUN_IPV4 = "198.18.0.1"
        const val TUN_IPV6 = "fc00::1"
        /** Alternated on each engine start; both are inside the tunnel. */
        val DNS_ADDRESSES = listOf("198.18.0.2", "198.18.0.3")
        const val MAPPED_NETWORK = "100.64.0.0"
        const val MAPPED_NETMASK = "255.192.0.0"
        private const val FILE = "proxy.json"

        fun fromJson(json: JSONObject) = ProxyConfig(
            json.getString("host").trim(), json.getInt("port"),
            json.optString("username", ""), json.optString("password", ""),
        )

        fun load(context: Context): ProxyConfig? = runCatching {
            fromJson(JSONObject(File(context.filesDir, FILE).readText()))
        }.getOrNull()

        fun save(context: Context, config: ProxyConfig) {
            val target = File(context.filesDir, FILE)
            val tmp = File(context.filesDir, "$FILE.tmp")
            tmp.writeText(config.toJson().toString())
            tmp.renameTo(target)
        }

        // No DNS lookups here: a hostname would be resolved outside the tunnel.
        private fun isIpLiteral(value: String): Boolean =
            Regex("^\\d{1,3}(\\.\\d{1,3}){3}$").matches(value) || (value.contains(':') && Regex("^[0-9a-fA-F:.]+$").matches(value))

        private fun quote(value: String) = "'" + value.replace("'", "''") + "'"
    }
}
