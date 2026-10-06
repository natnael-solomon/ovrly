package app.ovrly.capture

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Rect
import android.media.ExifInterface
import android.media.MediaMetadataRetriever
import android.os.SystemClock
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.Text
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.TextRecognizer
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * RES-02b (#103) benchmark replay of the shipped screen-text path. Debug androidTest only:
 * nothing here ships in the app. It runs only when the instrumentation argument
 * `res02Mode` is given, so ordinary connected test runs skip it.
 *
 * Inputs and outputs live in the app's external files directory under `res02/`
 * (`/sdcard/Android/data/app.ovrly/files/res02/`):
 *
 * - `res02Mode=images`: every `.png`/`.jpg` in `res02/images`, decoded from the exact
 *   file bytes and read twice per repetition: the full image through ML Kit directly
 *   (`full-image`) and through [ScreenTextReader.read] with the production text-region
 *   crop (`production-crop`). `res02Reps` sets the repetitions.
 * - `res02Mode=video`: every `.mp4`/`.webm` in `res02/video`, decoded at the 1 Hz probe
 *   times, scaled to the capture long edge and passed through the shipped [FrameSampler] and
 *   [ScreenTextReader]. Fixed five-second ticks are read too, so temporal and recognition
 *   misses of both policies can be told apart. Frames are decoded media, not screen
 *   captures.
 *
 * Writes JSON lines to `res02/out/<mode>-<res02Label>.jsonl`. Times are monotonic.
 */
@RunWith(AndroidJUnit4::class)
class ScreenTextReplayTest {
    private val arguments = InstrumentationRegistry.getArguments()
    private val root: File by lazy {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        File(context.getExternalFilesDir(null), "res02")
    }

    @Test
    fun replay() {
        val mode = arguments.getString("res02Mode")
        assumeTrue("RES-02 replay runs only with -e res02Mode", mode != null)
        val label = arguments.getString("res02Label") ?: "run"
        val out = File(root, "out").apply { mkdirs() }
        File(out, "$mode-$label.jsonl").bufferedWriter().use { writer ->
            val emit: (JSONObject) -> Unit = {
                writer.write(it.toString())
                writer.newLine()
                writer.flush()
            }
            when (mode) {
                "images" -> images(emit, arguments.getString("res02Reps")?.toInt() ?: 1)
                "video" -> video(emit)
                else -> error("unknown res02Mode $mode")
            }
        }
    }

    private fun images(emit: (JSONObject) -> Unit, reps: Int) {
        val files = inputs("images", IMAGE_TYPES)
        val initStarted = SystemClock.elapsedRealtimeNanos()
        val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
        val warm = Bitmap.createBitmap(MIN_EDGE, MIN_EDGE, Bitmap.Config.ARGB_8888)
        val warmed = runCatching { Tasks.await(recognizer.process(InputImage.fromBitmap(warm, 0))) }
        warm.recycle()
        val reader = ScreenTextReader { SystemClock.elapsedRealtime() }
        emit(
            JSONObject()
                .put("type", "init")
                .put("recognizer", Recognizer.toJson())
                .put("init_and_first_inference_ms", millisSince(initStarted))
                .put("status", if (warmed.isSuccess) "ok" else UNAVAILABLE)
                .put("files", files.size)
                .put("reps", reps)
        )
        for (rep in 0 until reps) {
            files.forEach { image(it, rep, recognizer, reader, emit) }
        }
        reader.close()
        recognizer.close()
    }

    private fun image(
        file: File,
        rep: Int,
        recognizer: TextRecognizer,
        reader: ScreenTextReader,
        emit: (JSONObject) -> Unit
    ) {
        val bytes = file.readBytes()
        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        fun row() = JSONObject()
            .put("type", "image")
            .put("file", file.name)
            .put("rep", rep)
            .put("sha256", sha256(bytes))
            .put("exif_orientation", orientation(file))
            .put("width", bitmap?.width ?: JSONObject.NULL)
            .put("height", bitmap?.height ?: JSONObject.NULL)
        if (bitmap == null) {
            emit(row().put("mode", "full-image").put("status", UNAVAILABLE).put("wall_ms", 0))
            return
        }
        val full = fullImage(recognizer, bitmap, row())
        emit(full)
        val started = SystemClock.elapsedRealtimeNanos()
        val text = reader.read(bitmap, 0)
        val crop = row().put("mode", "production-crop").put("wall_ms", millisSince(started))
        emit(frameJson(text, bitmap.width, bitmap.height, crop))
        // A timed-out ML Kit task is not cancelled and may still read the bitmap.
        val inUse = full.optString("error") == TIMEOUT || text.frameInUse
        if (!inUse) bitmap.recycle()
    }

    private fun fullImage(recognizer: TextRecognizer, bitmap: Bitmap, row: JSONObject): JSONObject {
        val started = SystemClock.elapsedRealtimeNanos()
        val result = runCatching {
            val task = recognizer.process(InputImage.fromBitmap(bitmap, 0))
            Tasks.await(task, TIMEOUT_S, TimeUnit.SECONDS)
        }
        row.put("mode", "full-image").put("wall_ms", millisSince(started))
        return result.fold(
            onSuccess = { row.put("status", "ok").put("lines", lines(it)) },
            onFailure = { row.put("status", UNAVAILABLE).put("error", it.javaClass.simpleName) }
        )
    }

    private fun video(emit: (JSONObject) -> Unit) {
        val files = inputs("video", VIDEO_TYPES)
        val reader = ScreenTextReader { SystemClock.elapsedRealtime() }
        emit(
            JSONObject()
                .put("type", "init")
                .put("recognizer", Recognizer.toJson())
                .put("sampling", SamplingPolicy.toJson())
                .put("files", files.size)
        )
        files.forEach { clip(it, reader, emit) }
        reader.close()
    }

    private fun clip(file: File, reader: ScreenTextReader, emit: (JSONObject) -> Unit) {
        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(file.path)
            val duration = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
            val durationMs = checkNotNull(duration).toLong()
            emit(
                JSONObject()
                    .put("type", "clip")
                    .put("file", file.name)
                    .put("duration_ms", durationMs)
                    .put("sha256", sha256(file.readBytes()))
            )
            val sampler = FrameSampler()
            var probeMs = 0L
            while (probeMs < durationMs) {
                emit(probe(file, retriever, sampler, reader, probeMs))
                probeMs += SamplingPolicy.PROBE_INTERVAL_MS
            }
        } finally {
            retriever.release()
        }
    }

    private fun probe(
        file: File,
        retriever: MediaMetadataRetriever,
        sampler: FrameSampler,
        reader: ScreenTextReader,
        probeMs: Long
    ): JSONObject {
        val decodeStarted = SystemClock.elapsedRealtimeNanos()
        val frame = frameAt(retriever, probeMs)
        val row = JSONObject()
            .put("type", "probe")
            .put("file", file.name)
            .put("probe_ms", probeMs)
            .put("decode_ms", millisSince(decodeStarted))
        if (frame == null) return row.put("status", "frame_unavailable")
        val samplerStarted = SystemClock.elapsedRealtimeNanos()
        val decision = sampler.decide(probeMs, reader.thumbnail(frame))
        val fixed = probeMs % FIXED_MS == 0L
        row.put("decision", decision.name.lowercase())
            .put("fixed_tick", fixed)
            .put("sampler_ms", millisSince(samplerStarted))
            .put("width", frame.width)
            .put("height", frame.height)
        val kept = decision == FrameSampler.Decision.CHANGED ||
            decision == FrameSampler.Decision.HEARTBEAT
        var inUse = false
        if (kept || fixed) {
            val started = SystemClock.elapsedRealtimeNanos()
            val text = reader.read(frame, probeMs)
            row.put("read_ms", millisSince(started))
            frameJson(text, frame.width, frame.height, row)
            inUse = text.frameInUse
        }
        // A timed-out recognition may still read the frame; leave it to the collector.
        if (!inUse) frame.recycle()
        return row
    }

    /** The frame at [ms], scaled to the capture long edge as `CaptureService` keeps it. */
    private fun frameAt(retriever: MediaMetadataRetriever, ms: Long): Bitmap? {
        val option = MediaMetadataRetriever.OPTION_CLOSEST
        return retriever.getFrameAtTime(ms * US_PER_MS, option)?.let(::toCaptureSize)
    }

    private fun toCaptureSize(raw: Bitmap): Bitmap {
        val longEdge = maxOf(raw.width, raw.height)
        if (longEdge <= CaptureLimits.FRAME_LONG_EDGE) return raw
        val factor = CaptureLimits.FRAME_LONG_EDGE.toDouble() / longEdge
        val width = Math.round(raw.width * factor).toInt()
        val height = Math.round(raw.height * factor).toInt()
        val scaled = Bitmap.createScaledBitmap(raw, width, height, true)
        if (scaled !== raw) raw.recycle()
        return scaled
    }

    private fun frameJson(text: FrameText, width: Int, height: Int, row: JSONObject): JSONObject {
        val failed = text.status == FrameStatus.FAILED
        row.put("frame_status", text.status.wireName)
            .put("regions", text.regions)
            .put("failed_regions", text.failedRegions)
            .put("recognition_ms", text.recognitionMs)
            .put("status", if (failed) UNAVAILABLE else "ok")
        val lines = JSONArray()
        for (observation in text.observations) {
            val box = observation.box
            val pixels = listOf(
                box[0] * width / TextRegions.BOX_SCALE,
                box[1] * height / TextRegions.BOX_SCALE,
                box[2] * width / TextRegions.BOX_SCALE,
                box[3] * height / TextRegions.BOX_SCALE
            )
            lines.put(
                JSONObject()
                    .put("text", observation.text)
                    .put("box_normalized", JSONArray(box))
                    .put("box", JSONArray(pixels))
            )
        }
        return row.put("lines", lines)
    }

    private fun lines(text: Text): JSONArray {
        val lines = JSONArray()
        for (line in text.textBlocks.flatMap { it.lines }) {
            val elements = JSONArray()
            for (element in line.elements) {
                elements.put(
                    JSONObject()
                        .put("text", element.text)
                        .put("box", box(element.boundingBox))
                        .put("confidence", element.confidence.toDouble())
                )
            }
            lines.put(
                JSONObject()
                    .put("text", line.text)
                    .put("box", box(line.boundingBox))
                    .put("confidence", line.confidence.toDouble())
                    .put("elements", elements)
            )
        }
        return lines
    }

    private fun inputs(directory: String, types: Set<String>): List<File> {
        val files = File(root, directory)
            .listFiles { file -> file.extension.lowercase() in types }
            ?.sortedBy { it.name }
            .orEmpty()
        check(files.isNotEmpty()) { "no inputs in ${root.path}/$directory" }
        return files
    }

    private fun box(rect: Rect?): Any =
        rect?.let { JSONArray(listOf(it.left, it.top, it.right, it.bottom)) } ?: JSONObject.NULL

    private fun orientation(file: File): Int = runCatching {
        ExifInterface(file.path).getAttributeInt(ExifInterface.TAG_ORIENTATION, UNDEFINED)
    }.getOrDefault(UNDEFINED)

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    private fun millisSince(startNanos: Long): Double =
        (SystemClock.elapsedRealtimeNanos() - startNanos) / NANOS_PER_MS

    private companion object {
        val IMAGE_TYPES = setOf("png", "jpg", "jpeg")
        val VIDEO_TYPES = setOf("mp4", "webm")
        const val UNAVAILABLE = "OCR_UNAVAILABLE"
        const val TIMEOUT = "TimeoutException"
        const val UNDEFINED = ExifInterface.ORIENTATION_UNDEFINED
        const val MIN_EDGE = 32
        const val TIMEOUT_S = 30L
        const val FIXED_MS = 5_000L
        const val US_PER_MS = 1_000L
        const val NANOS_PER_MS = 1_000_000.0
    }
}
