package app.ovrly.capture

import android.graphics.Bitmap
import android.util.Log
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.Text
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import java.util.concurrent.ExecutionException
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import kotlin.math.abs
import org.json.JSONArray
import org.json.JSONObject

/**
 * Screen-text sampling policy (AN-06, #100). The screen is probed once a second; a probe is
 * kept when its 64x64 grayscale thumbnail differs from the last kept one by a mean absolute
 * difference of at least [CHANGE_THRESHOLD], or when [HEARTBEAT_MS] passed without a kept
 * frame. At most [MAX_FRAMES_PER_MINUTE] frames are kept in any 60 s window; the rest are
 * counted as capped. Probe rate, threshold and heartbeat are the RES-02 workstation values
 * (#14); the cap is this app's own bound on recognition work.
 */
internal object SamplingPolicy {
    const val NAME = "change_triggered"
    const val PROBE_INTERVAL_MS = 1_000L
    const val THUMBNAIL_EDGE = 64
    const val CHANGE_THRESHOLD = 12
    const val HEARTBEAT_MS = 5_000L
    const val MAX_FRAMES_PER_MINUTE = 20
    const val WINDOW_MS = 60_000L

    fun toJson(): JSONObject = JSONObject()
        .put("policy", NAME)
        .put("probe_interval_ms", PROBE_INTERVAL_MS)
        .put("thumbnail_edge", THUMBNAIL_EDGE)
        .put("change_threshold", CHANGE_THRESHOLD)
        .put("heartbeat_ms", HEARTBEAT_MS)
        .put("max_frames_per_minute", MAX_FRAMES_PER_MINUTE)
        .put("frame_long_edge", CaptureLimits.FRAME_LONG_EDGE)
}

/** The on-device recognizer. Keep [VERSION] equal to the dependency in `app/build.gradle.kts`. */
internal object Recognizer {
    const val NAME = "mlkit-text-recognition-latin-bundled"
    const val VERSION = "16.0.1"

    /** Per-region recognition timeout; ML Kit does not cancel the task when it passes. */
    const val TIMEOUT_MS = 5_000L

    fun toJson(): JSONObject = JSONObject().put("name", NAME).put("version", VERSION)
}

/** Decides which probed frames are recognized. Times are capture-relative milliseconds. */
internal class FrameSampler {
    enum class Decision { UNCHANGED, CHANGED, HEARTBEAT, CAPPED }

    private var lastProbeMs = -SamplingPolicy.PROBE_INTERVAL_MS
    private var lastKeptMs: Long? = null
    private var lastKept: IntArray? = null
    private val kept = ArrayDeque<Long>()

    /** True when a probe is due; cheap, so it runs before any pixel work. */
    fun probeDue(elapsedMs: Long): Boolean =
        elapsedMs - lastProbeMs >= SamplingPolicy.PROBE_INTERVAL_MS

    /** Classifies one probe given its grayscale thumbnail; CHANGED and HEARTBEAT are kept. */
    fun decide(elapsedMs: Long, thumbnail: IntArray): Decision {
        lastProbeMs = elapsedMs
        val previous = lastKept
        val since = lastKeptMs?.let { elapsedMs - it }
        val decision = when {
            previous == null ||
                meanAbsoluteDifference(previous, thumbnail) >= SamplingPolicy.CHANGE_THRESHOLD ->
                Decision.CHANGED

            since != null && since >= SamplingPolicy.HEARTBEAT_MS -> Decision.HEARTBEAT

            else -> Decision.UNCHANGED
        }
        while (kept.isNotEmpty() && elapsedMs - kept.first() >= SamplingPolicy.WINDOW_MS) {
            kept.removeFirst()
        }
        val wanted = decision != Decision.UNCHANGED
        val capped = wanted && kept.size >= SamplingPolicy.MAX_FRAMES_PER_MINUTE
        if (wanted && !capped) {
            kept.addLast(elapsedMs)
            lastKept = thumbnail
            lastKeptMs = elapsedMs
        }
        return if (capped) Decision.CAPPED else decision
    }

    companion object {
        fun meanAbsoluteDifference(a: IntArray, b: IntArray): Int {
            require(a.size == b.size && a.isNotEmpty()) { "thumbnails must match" }
            return (a.indices.sumOf { abs(a[it] - b[it]).toLong() } / a.size).toInt()
        }
    }
}

/** Grayscale and text-region helpers on plain pixel arrays, so they run in unit tests. */
internal object TextRegions {
    const val BOX_SCALE = 10_000
    private const val RED_WEIGHT = 299
    private const val GREEN_WEIGHT = 587
    private const val BLUE_WEIGHT = 114
    private const val WEIGHT_TOTAL = 1_000
    private const val RED_SHIFT = 16
    private const val GREEN_SHIFT = 8
    private const val CHANNEL = 0xff
    private const val EDGE_STEP = 40
    private const val ROW_EDGE_PERMILLE = 30
    private const val ROW_GAP = 4
    private const val MIN_BAND_HEIGHT = 6
    private const val PADDING = 8

    /** ML Kit rejects images smaller than 32 px on a side; smaller crops are grown to it. */
    const val MIN_EDGE = 32

    /** More bands than this are read as the whole frame, so a frame has at most this many. */
    const val MAX_REGIONS = 8
    private const val FULL_FRAME_PERCENT = 70
    private const val PERCENT = 100
    private const val PERMILLE = 1_000

    /** A pixel rectangle `[left, right)` x `[top, bottom)` within the frame. */
    data class Region(val left: Int, val top: Int, val right: Int, val bottom: Int) {
        val width: Int get() = right - left
        val height: Int get() = bottom - top
    }

    fun luminance(argb: IntArray): IntArray = IntArray(argb.size) {
        val pixel = argb[it]
        val red = pixel shr RED_SHIFT and CHANNEL
        val green = pixel shr GREEN_SHIFT and CHANNEL
        val blue = pixel and CHANNEL
        (red * RED_WEIGHT + green * GREEN_WEIGHT + blue * BLUE_WEIGHT) / WEIGHT_TOTAL
    }

    /**
     * Likely text: horizontal bands whose rows have many sharp horizontal brightness steps,
     * cropped to the columns where those steps occur and padded. Returns the whole frame when
     * the bands cover most of it, and nothing when no band qualifies.
     */
    fun find(gray: IntArray, width: Int, height: Int): List<Region> {
        require(gray.size == width * height && width > 1 && height > 0)
        val texty = BooleanArray(height) { y ->
            edgeCount(gray, width, y) * PERMILLE >= width * ROW_EDGE_PERMILLE
        }
        val bands = bands(texty)
        val covered = bands.sumOf { it.last - it.first + 1 }
        return when {
            bands.isEmpty() -> emptyList()

            covered * PERCENT >= height * FULL_FRAME_PERCENT || bands.size > MAX_REGIONS ->
                listOf(Region(0, 0, width, height))

            else -> bands.map { atLeast(columns(gray, width, height, it), width, height) }
        }
    }

    /** Grows [region] around its centre to at least [MIN_EDGE] per side, within the frame. */
    fun atLeast(region: Region, width: Int, height: Int): Region {
        val (left, right) = grow(region.left, region.right, width)
        val (top, bottom) = grow(region.top, region.bottom, height)
        return Region(left, top, right, bottom)
    }

    private fun grow(start: Int, end: Int, size: Int): Pair<Int, Int> {
        val length = minOf(MIN_EDGE, size)
        if (end - start >= length) return start to end
        val from = (start - (length - (end - start)) / 2).coerceIn(0, size - length)
        return from to from + length
    }

    /** Normalizes a box in frame pixels to the contract's 0..10000 coordinates. */
    fun normalize(region: Region, width: Int, height: Int): IntArray = intArrayOf(
        scale(region.left, width),
        scale(region.top, height),
        scale(region.right, width),
        scale(region.bottom, height)
    )

    private fun scale(value: Int, size: Int): Int =
        (value.coerceIn(0, size).toLong() * BOX_SCALE / size).toInt()

    private fun edgeCount(gray: IntArray, width: Int, y: Int): Int {
        val row = y * width
        return (0 until width - 1).count { abs(gray[row + it + 1] - gray[row + it]) >= EDGE_STEP }
    }

    /** Groups text rows separated by at most [ROW_GAP] other rows; drops thin bands. */
    private fun bands(texty: BooleanArray): List<IntRange> {
        val bands = mutableListOf<IntRange>()
        for (y in texty.indices.filter { texty[it] }) {
            val last = bands.lastOrNull()
            if (last != null && y - last.last <= ROW_GAP + 1) {
                bands[bands.lastIndex] = last.first..y
            } else {
                bands += y..y
            }
        }
        return bands.filter { it.last - it.first + 1 >= MIN_BAND_HEIGHT }
    }

    private fun columns(gray: IntArray, width: Int, height: Int, band: IntRange): Region {
        val edges = band.flatMap { y ->
            (0 until width - 1).filter { x ->
                abs(gray[y * width + x + 1] - gray[y * width + x]) >= EDGE_STEP
            }
        }
        return Region(
            (edges.min() - PADDING).coerceAtLeast(0),
            (band.first - PADDING).coerceAtLeast(0),
            (edges.max() + 2 + PADDING).coerceAtMost(width),
            (band.last + 1 + PADDING).coerceAtMost(height)
        )
    }
}

/** One recognized line: text, box normalized to 0..10000 and the frame's capture time. */
internal data class TextObservation(val text: String, val box: List<Int>, val framePtsMs: Long) {
    fun toJson(): JSONObject = JSONObject()
        .put("text", text)
        .put("box", JSONArray(box))
        .put("frame_pts", framePtsMs)
}

/** What happened to one frame the sampler wanted. */
internal enum class FrameStatus(val wireName: String) {
    RECOGNIZED("recognized"),
    NO_TEXT_REGIONS("no_text_regions"),
    FAILED("failed"),

    /** Wanted by the change trigger but refused by the per-minute cap; never in a chunk. */
    CAPPED("capped")
}

/** The result of one kept frame; [recognitionMs] is the on-device recognition time. */
internal data class FrameText(
    val framePtsMs: Long,
    val status: FrameStatus,
    val regions: Int,
    val observations: List<TextObservation>,
    val recognitionMs: Long = 0,
    /** Regions whose recognition failed; the frame is [FrameStatus.FAILED] only if all did. */
    val failedRegions: Int = 0,
    /**
     * A recognition timed out and ML Kit may still read the frame, so the caller must not
     * recycle it; the garbage collector frees it. Never written to a chunk.
     */
    val frameInUse: Boolean = false
)

/** The outcome of reading one region. */
internal sealed interface RegionRead {
    data class Lines(val observations: List<TextObservation>) : RegionRead

    data object Failed : RegionRead

    /** The task was not cancelled and may still be reading the region's bitmap. */
    data object TimedOut : RegionRead
}

/** What [readRegions] made of a frame's regions. */
internal data class RegionsRead(
    val observations: List<TextObservation>,
    val failed: Int,
    val timedOut: Boolean
)

/**
 * Reads [regions] one at a time, so one failed region keeps the others' text. After the first
 * timeout the remaining regions are skipped and counted as failed: ML Kit runs one task at a
 * time, so they would queue behind the task that is still running.
 */
internal fun readRegions(
    regions: List<TextRegions.Region>,
    read: (TextRegions.Region) -> RegionRead
): RegionsRead {
    val observations = mutableListOf<TextObservation>()
    var failed = 0
    var timedOut = false
    for (region in regions) {
        if (timedOut) {
            failed++
            continue
        }
        when (val result = read(region)) {
            is RegionRead.Lines -> observations += result.observations

            RegionRead.Failed -> failed++

            RegionRead.TimedOut -> {
                failed++
                timedOut = true
            }
        }
    }
    return RegionsRead(observations, failed, timedOut)
}

/**
 * Reads screen text on the device with the bundled ML Kit Latin model. Only text
 * observations leave the device; frames stay in private storage. Recognition blocks the
 * calling thread, so callers must not hold a lock that [close] or capture release needs.
 */
internal class ScreenTextReader(private val clock: () -> Long) {
    private val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)

    /** A grayscale [SamplingPolicy.THUMBNAIL_EDGE] square of [frame] for [FrameSampler]. */
    fun thumbnail(frame: Bitmap): IntArray {
        val edge = SamplingPolicy.THUMBNAIL_EDGE
        val scaled = Bitmap.createScaledBitmap(frame, edge, edge, true)
        val pixels = IntArray(edge * edge)
        scaled.getPixels(pixels, 0, edge, 0, 0, edge, edge)
        if (scaled !== frame) scaled.recycle()
        return TextRegions.luminance(pixels)
    }

    /** Runs recognition on the likely text regions of [frame]. */
    fun read(frame: Bitmap, elapsedMs: Long): FrameText {
        val started = clock()
        val pixels = IntArray(frame.width * frame.height)
        frame.getPixels(pixels, 0, frame.width, 0, 0, frame.width, frame.height)
        val regions = TextRegions.find(TextRegions.luminance(pixels), frame.width, frame.height)
        val read = readRegions(regions) { recognize(frame, it, elapsedMs) }
        val status = when {
            regions.isEmpty() -> FrameStatus.NO_TEXT_REGIONS
            read.failed == regions.size -> FrameStatus.FAILED
            else -> FrameStatus.RECOGNIZED
        }
        return FrameText(
            elapsedMs,
            status,
            regions.size,
            read.observations,
            clock() - started,
            read.failed,
            frameInUse = read.timedOut
        )
    }

    fun close() {
        recognizer.close()
    }

    /** Reads one region; a failure logs only the error type. */
    private fun recognize(frame: Bitmap, region: TextRegions.Region, elapsedMs: Long): RegionRead {
        val crop = Bitmap.createBitmap(frame, region.left, region.top, region.width, region.height)
        // Tasks.await does not cancel the task on timeout, so a timed-out task may still read
        // the crop (or the frame itself when the crop is the whole frame): it is not recycled.
        var inUse = false
        return try {
            val text = Tasks.await(
                recognizer.process(InputImage.fromBitmap(crop, 0)),
                Recognizer.TIMEOUT_MS,
                TimeUnit.MILLISECONDS
            )
            RegionRead.Lines(lines(text, region, frame, elapsedMs))
        } catch (error: TimeoutException) {
            inUse = true
            logFailure(error)
            RegionRead.TimedOut
        } catch (error: ExecutionException) {
            logFailure(error.cause ?: error)
            RegionRead.Failed
        } catch (error: InterruptedException) {
            Thread.currentThread().interrupt()
            inUse = true
            logFailure(error)
            RegionRead.TimedOut
        } finally {
            if (!inUse && crop !== frame) crop.recycle()
        }
    }

    private fun lines(
        text: Text,
        region: TextRegions.Region,
        frame: Bitmap,
        elapsedMs: Long
    ): List<TextObservation> = text.textBlocks.flatMap { it.lines }.mapNotNull { line ->
        val box = line.boundingBox ?: return@mapNotNull null
        val absolute = TextRegions.Region(
            region.left + box.left,
            region.top + box.top,
            region.left + box.right,
            region.top + box.bottom
        )
        TextObservation(
            line.text,
            TextRegions.normalize(absolute, frame.width, frame.height).toList(),
            elapsedMs
        )
    }

    private fun logFailure(error: Throwable) {
        Log.w(TAG, "Text recognition failed for a region: ${error.javaClass.simpleName}")
    }

    private companion object {
        const val TAG = "OvrlyCapture"
    }
}
