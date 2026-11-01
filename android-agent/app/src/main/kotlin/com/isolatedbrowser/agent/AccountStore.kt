package com.isolatedbrowser.agent

import android.content.Context
import android.util.Base64
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

/**
 * The account this Android user holds, so any PC can rebuild it from the phone: ID, name, folder,
 * the proxy's measured exit, and an optional PIN. The proxy itself stays in [ProxyConfig].
 * Encrypted like the proxy; the PIN is kept only as a salted hash.
 */
object AccountStore {
    private const val FILE = "account.enc"
    private const val PIN_ITERATIONS = 20000
    /** Fields a PC may set. The PIN is changed only through [setPin] and [clearPin]. */
    private val DETAIL_KEYS = listOf("profile_id", "name", "group", "exit_ip", "timezone")

    fun load(context: Context): JSONObject =
        SecretStore.read(File(context.filesDir, FILE))?.let { runCatching { JSONObject(it) }.getOrNull() } ?: JSONObject()

    /** Details without the PIN hash, for status and export. */
    fun details(context: Context): JSONObject {
        val saved = load(context)
        return JSONObject().apply {
            DETAIL_KEYS.forEach { key -> if (saved.has(key)) put(key, saved.get(key)) }
            put("pin_set", saved.has("pin_hash"))
        }
    }

    fun saveDetails(context: Context, details: JSONObject) {
        val saved = load(context)
        DETAIL_KEYS.forEach { key -> if (details.has(key)) saved.put(key, details.get(key)) }
        save(context, saved)
    }

    fun pinSet(context: Context) = load(context).has("pin_hash")

    fun pinMatches(context: Context, pin: String?): Boolean {
        val saved = load(context)
        if (!saved.has("pin_hash")) return true
        if (pin == null) return false
        val salt = Base64.decode(saved.getString("pin_salt"), Base64.NO_WRAP)
        return MessageDigest.isEqual(hash(pin, salt), Base64.decode(saved.getString("pin_hash"), Base64.NO_WRAP))
    }

    fun setPin(context: Context, pin: String) {
        require(Regex("^\\d{4}$").matches(pin)) { "PIN must be 4 digits" }
        val salt = ByteArray(16).also { SecureRandom().nextBytes(it) }
        val saved = load(context)
        saved.put("pin_salt", Base64.encodeToString(salt, Base64.NO_WRAP))
        saved.put("pin_hash", Base64.encodeToString(hash(pin, salt), Base64.NO_WRAP))
        save(context, saved)
    }

    fun clearPin(context: Context) {
        val saved = load(context)
        saved.remove("pin_salt")
        saved.remove("pin_hash")
        save(context, saved)
    }

    private fun save(context: Context, account: JSONObject) {
        SecretStore.write(File(context.filesDir, FILE), account.toString())
    }

    private fun hash(pin: String, salt: ByteArray): ByteArray =
        SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
            .generateSecret(PBEKeySpec(pin.toCharArray(), salt, PIN_ITERATIONS, 256)).encoded
}
