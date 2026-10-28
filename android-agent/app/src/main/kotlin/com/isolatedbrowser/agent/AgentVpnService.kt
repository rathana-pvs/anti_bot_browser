package com.isolatedbrowser.agent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Intent
import android.net.IpPrefix
import android.net.VpnService
import android.os.ParcelFileDescriptor
import android.os.Process
import android.util.Log
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Routes all traffic of this Android user into a tun device handled by hev-socks5-tunnel.
 *
 * Fail-closed: the tun interface is kept up whenever the service runs, even with no proxy or
 * a dead engine, so packets are dropped instead of leaving through the phone's own network.
 * Only the proxy endpoint itself is routed outside the tunnel.
 */
class AgentVpnService : VpnService() {

    private var tun: ParcelFileDescriptor? = null
    private var activeConfig: ProxyConfig? = null
    private var dnsSlot = 0
    private val watchdog = Executors.newSingleThreadScheduledExecutor()
    @Volatile var lastError: String? = null
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        watchdog.scheduleWithFixedDelay({ checkEngine() }, 5, 5, TimeUnit.SECONDS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID, notification())
        if (intent?.action == ACTION_STOP) {
            shutdown()
            return START_NOT_STICKY
        }
        // Always-on VPN starts the service with the VpnService action; treat every start as "connect".
        synchronized(this) { connect(ProxyConfig.load(this)) }
        return START_STICKY
    }

    override fun onRevoke() {
        lastError = "VPN permission was revoked"
        shutdown()
    }

    override fun onDestroy() {
        watchdog.shutdownNow()
        stopEngine()
        tun?.close()
        tun = null
        if (instance === this) instance = null
        super.onDestroy()
    }

    /** Apply a new proxy. The tun stays open while the engine restarts, so nothing leaks meanwhile. */
    @Synchronized
    fun applyConfig(config: ProxyConfig) {
        ProxyConfig.save(this, config)
        connect(config)
    }

    @Synchronized
    private fun connect(config: ProxyConfig?) {
        // A restarted engine forgets its mapped DNS answers, including the address Android's
        // Private DNS resolved for its server. Changing the VPN's DNS address on every start makes
        // Android resolve again. The new interface is established before the old one closes, so
        // there is no moment without a tunnel.
        dnsSlot = (dnsSlot + 1) % ProxyConfig.DNS_ADDRESSES.size
        val dnsAddress = ProxyConfig.DNS_ADDRESSES[dnsSlot]
        val next = establish(config, dnsAddress) ?: run {
            lastError = "The VPN could not be started. Check that VPN permission is granted."
            return
        }
        stopEngine()
        tun?.close()
        tun = next
        activeConfig = config
        if (config == null) {
            lastError = "No proxy is configured; all traffic is blocked."
            return
        }
        val yaml = File(filesDir, "tunnel.yml")
        yaml.writeText(config.toTunnelYaml(File(filesDir, "tunnel.log"), dnsAddress))
        lastError = if (TunnelEngine.TProxyStartService(yaml.absolutePath, tun!!.fd)) null
        else "The tunnel engine did not start."
    }

    private fun establish(config: ProxyConfig?, dnsAddress: String): ParcelFileDescriptor? {
        val builder = Builder()
            .setSession("Network Agent")
            .setMtu(ProxyConfig.MTU)
            .addAddress(ProxyConfig.TUN_IPV4, 32)
            .addAddress(ProxyConfig.TUN_IPV6, 128)
            .addRoute("0.0.0.0", 0)
            .addRoute("::", 0)
            .addDnsServer(dnsAddress)
            .setBlocking(false)
        config?.let { builder.excludeRoute(IpPrefix(it.address(), if (it.host.contains(':')) 128 else 32)) }
        return try {
            builder.establish()
        } catch (error: Exception) {
            Log.e(TAG, "establish failed", error)
            null
        }
    }

    private fun stopEngine() {
        if (TunnelEngine.TProxyIsRunning()) TunnelEngine.TProxyStopService()
    }

    private fun checkEngine() {
        val config = activeConfig ?: return
        if (tun != null && !TunnelEngine.TProxyIsRunning()) {
            lastError = "The tunnel engine stopped; restarting. Traffic is blocked meanwhile."
            synchronized(this) { connect(config) }
        }
    }

    @Synchronized
    fun shutdown() {
        stopEngine()
        tun?.close()
        tun = null
        activeConfig = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    fun status(): Map<String, Any?> {
        val stats = runCatching { TunnelEngine.TProxyGetStats() }.getOrNull()
        return mapOf(
            "user_id" to userId(),
            "tunnel_up" to (tun != null),
            "engine_running" to TunnelEngine.TProxyIsRunning(),
            "proxy" to activeConfig?.let { "${it.host}:${it.port}" },
            "always_on" to isAlwaysOn,
            "lockdown" to isLockdownEnabled,
            "tx_bytes" to stats?.getOrNull(1),
            "rx_bytes" to stats?.getOrNull(3),
            "last_error" to lastError,
        )
    }

    private fun notification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Network", NotificationManager.IMPORTANCE_MIN))
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentTitle("Network agent running")
            .setOngoing(true)
            .build()
    }

    companion object {
        const val ACTION_STOP = "com.isolatedbrowser.agent.STOP"
        private const val TAG = "AgentVpn"
        private const val CHANNEL = "agent"
        private const val NOTIFICATION_ID = 1

        @Volatile var instance: AgentVpnService? = null
            private set

        fun userId(): Int = Process.myUid() / 100000
    }
}
