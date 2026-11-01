package com.isolatedbrowser.agent

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.MediaStore
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/**
 * Saves a file the worker streams in (`adb shell content write --user N --uri
 * content://AUTHORITY/media?name=NAME&mime=MIME < file`) into this user's gallery, owned by this app,
 * like any app saving a download. Facebook's own gallery picker then shows it as the newest item.
 * This app may delete only what it added, so a job's media never stays on the phone.
 */
object MediaWriter {
    private val NAME = Regex("^[A-Za-z0-9._-]{1,80}$")
    /** Outcome of each write by file name, read back with the `media_status` command. */
    private val results = ConcurrentHashMap<String, JSONObject>()

    fun open(context: Context, uri: Uri): ParcelFileDescriptor {
        val name = requireNotNull(uri.getQueryParameter("name")?.takeIf { NAME.matches(it) }) { "invalid name" }
        val mime = requireNotNull(uri.getQueryParameter("mime")?.takeIf { it == "image/jpeg" || it == "image/png" || it == "video/mp4" }) {
            "mime must be image/jpeg, image/png or video/mp4"
        }
        val video = mime.startsWith("video/")
        val (read, write) = ParcelFileDescriptor.createReliablePipe()
        results[name] = JSONObject().put("state", "writing")
        Thread {
            results[name] = runCatching { save(context, read, name, mime, video) }
                .getOrElse { JSONObject().put("state", "failed").put("error", it.message ?: it.javaClass.simpleName) }
        }.apply { isDaemon = true }.start()
        return write
    }

    fun status(name: String): JSONObject = results[name] ?: JSONObject().put("state", "unknown")

    fun delete(context: Context, uri: String): Int = context.contentResolver.delete(Uri.parse(uri), null, null)

    private fun save(context: Context, source: ParcelFileDescriptor, name: String, mime: String, video: Boolean): JSONObject {
        val collection = if (video) MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        else MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            put(MediaStore.MediaColumns.MIME_TYPE, mime)
            put(MediaStore.MediaColumns.RELATIVE_PATH, if (video) "Movies/" else "Pictures/")
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val resolver = context.contentResolver
        val item = requireNotNull(resolver.insert(collection, values)) { "the gallery refused the file" }
        try {
            var size = 0L
            ParcelFileDescriptor.AutoCloseInputStream(source).use { input ->
                requireNotNull(resolver.openOutputStream(item)) { "cannot write the file" }.use { output -> size = input.copyTo(output) }
            }
            require(size > 0) { "no data was received" }
            resolver.update(item, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
            return JSONObject().put("state", "saved").put("uri", item.toString()).put("size", size)
        } catch (error: Throwable) {
            resolver.delete(item, null, null)
            throw error
        }
    }
}
