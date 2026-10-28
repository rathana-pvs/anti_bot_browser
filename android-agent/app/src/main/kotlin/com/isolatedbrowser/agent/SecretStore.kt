package com.isolatedbrowser.agent

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Small encrypted file store. The AES-256-GCM key lives in Android Keystore and never leaves it,
 * so a copy of the app's files is useless without this app on this device and user.
 */
object SecretStore {
    private const val KEYSTORE = "AndroidKeyStore"
    private const val ALIAS = "proxy-config"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"

    fun write(file: File, plaintext: String) {
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val sealed = Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + ":" +
            Base64.encodeToString(cipher.doFinal(plaintext.toByteArray()), Base64.NO_WRAP)
        val tmp = File(file.parentFile, "${file.name}.tmp")
        tmp.writeText(sealed)
        tmp.renameTo(file)
    }

    /** Null when the file is missing or cannot be decrypted, e.g. after the key was lost. */
    fun read(file: File): String? = runCatching {
        val (iv, data) = file.readText().split(':', limit = 2).map { Base64.decode(it, Base64.NO_WRAP) }
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv)) }
        String(cipher.doFinal(data))
    }.getOrNull()

    private fun key(): SecretKey {
        val store = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE).apply {
            init(
                KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build(),
            )
        }.generateKey()
    }
}
