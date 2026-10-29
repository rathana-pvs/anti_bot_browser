package com.isolatedbrowser.agent

import android.app.Activity
import android.content.Intent
import android.net.VpnService
import android.os.Bundle

/**
 * Starts the VPN service. The worker launches it with `am start --user N`, which is allowed to
 * start a foreground service where a background call would not be. It never stops the VPN, so
 * another app launching it can only turn protection on.
 */
class ControlActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // prepare() returns null only when VPN permission is already granted for this user.
        if (VpnService.prepare(this) == null) {
            startForegroundService(Intent(this, AgentVpnService::class.java))
        }
        finish()
    }
}
