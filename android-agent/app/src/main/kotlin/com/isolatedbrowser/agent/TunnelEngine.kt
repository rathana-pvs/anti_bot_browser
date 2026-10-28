package com.isolatedbrowser.agent

/**
 * JNI bridge to hev-socks5-tunnel. The native library registers these methods on this
 * class (PKGNAME/CLSNAME in app/build.gradle.kts), so names and signatures must match.
 */
object TunnelEngine {
    init {
        System.loadLibrary("hev-socks5-tunnel")
    }

    @JvmStatic external fun TProxyStartService(configPath: String, fd: Int): Boolean
    @JvmStatic external fun TProxyStopService(): Boolean
    @JvmStatic external fun TProxyIsRunning(): Boolean

    /** tx packets, tx bytes, rx packets, rx bytes. */
    @JvmStatic external fun TProxyGetStats(): LongArray?
}
