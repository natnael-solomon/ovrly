package app.ovrly.fixtures;

import android.media.Image;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.media.MediaMuxer;
import android.os.SystemClock;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;

/**
 * Generates synthetic share fixtures on the device: a short H.264 video, an AAC audio-only file
 * and a text file. No media is committed and no personal media is used. Framework-only Java,
 * see {@link ShareFixtureProvider}.
 */
public final class FixtureMedia {
    /** 1.5 s of 176x144 H.264 video in an MP4 container. */
    public static final String VIDEO = "clip.mp4";
    /** About 1 s of AAC audio in an MP4 container, without a video track. */
    public static final String AUDIO = "tone.m4a";
    /** Plain text bytes; a provider may still call it video. */
    public static final String TEXT = "notes.mp4";
    /** Never generated: reads fail as if the source app deleted the file. */
    public static final String MISSING = "missing.mp4";

    private static final int WIDTH = 176;
    private static final int HEIGHT = 144;
    private static final int FRAME_RATE = 10;
    private static final int FRAMES = 15;
    private static final int SAMPLE_RATE = 44_100;
    private static final int SAMPLES_PER_BUFFER = 1024;
    private static final int AUDIO_BUFFERS = 43;
    private static final long TIMEOUT_US = 10_000L;
    private static final long DEADLINE_MS = 30_000L;
    private static final long MICROS = 1_000_000L;
    private static final byte NEUTRAL_CHROMA = (byte) 128;

    private FixtureMedia() {
    }

    public static boolean isKnownName(String name) {
        return VIDEO.equals(name) || AUDIO.equals(name) || TEXT.equals(name)
            || MISSING.equals(name);
    }

    /** Creates any fixture that does not exist yet in {@code directory}. */
    public static synchronized void ensure(File directory) throws IOException {
        if (!directory.isDirectory() && !directory.mkdirs()) {
            throw new IOException("Cannot create the fixture directory");
        }
        File video = new File(directory, VIDEO);
        if (!video.isFile()) {
            encode(video, videoFormat(), FRAMES, FixtureMedia::fillFrame, FixtureMedia::framePts);
        }
        File audio = new File(directory, AUDIO);
        if (!audio.isFile()) {
            encode(audio, audioFormat(), AUDIO_BUFFERS, FixtureMedia::fillTone, FixtureMedia::tonePts);
        }
        File text = new File(directory, TEXT);
        if (!text.isFile()) {
            File partial = new File(directory, TEXT + ".part");
            try (OutputStream output = new FileOutputStream(partial)) {
                output.write("This is not a video.\n".getBytes(StandardCharsets.UTF_8));
            }
            rename(partial, text);
        }
    }

    private interface Filler {
        int fill(MediaCodec codec, int index, int sample);
    }

    private interface Clock {
        long ptsUs(int sample);
    }

    private static MediaFormat videoFormat() {
        MediaFormat format = MediaFormat.createVideoFormat(
            MediaFormat.MIMETYPE_VIDEO_AVC, WIDTH, HEIGHT
        );
        format.setInteger(MediaFormat.KEY_BIT_RATE, 250_000);
        format.setInteger(MediaFormat.KEY_FRAME_RATE, FRAME_RATE);
        format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
        return format;
    }

    private static MediaFormat audioFormat() {
        MediaFormat format = MediaFormat.createAudioFormat(
            MediaFormat.MIMETYPE_AUDIO_AAC, SAMPLE_RATE, 1
        );
        format.setInteger(
            MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC
        );
        format.setInteger(MediaFormat.KEY_BIT_RATE, 64_000);
        format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, SAMPLES_PER_BUFFER * 2);
        return format;
    }

    private static long framePts(int frame) {
        return frame * MICROS / FRAME_RATE;
    }

    private static long tonePts(int buffer) {
        return buffer * (long) SAMPLES_PER_BUFFER * MICROS / SAMPLE_RATE;
    }

    /** Planar, semi-planar or flexible YUV 4:2:0, whichever the encoder was configured for. */
    private static int colorFormat(MediaCodec codec) {
        int[] supported = codec.getCodecInfo()
            .getCapabilitiesForType(MediaFormat.MIMETYPE_VIDEO_AVC).colorFormats;
        int[] preferred = {
            MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar,
            MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar
        };
        for (int wanted : preferred) {
            for (int format : supported) {
                if (format == wanted) {
                    return format;
                }
            }
        }
        return MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible;
    }

    private static int fillFrame(MediaCodec codec, int index, int frame) {
        int size = WIDTH * HEIGHT * 3 / 2;
        MediaFormat input = codec.getInputFormat();
        int format = input.containsKey(MediaFormat.KEY_COLOR_FORMAT)
            ? input.getInteger(MediaFormat.KEY_COLOR_FORMAT)
            : MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible;
        if (format == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible) {
            Image image = codec.getInputImage(index);
            if (image == null) {
                throw new IllegalStateException("Encoder offered no input image");
            }
            Image.Plane[] planes = image.getPlanes();
            for (int plane = 0; plane < planes.length; plane++) {
                int width = plane == 0 ? WIDTH : WIDTH / 2;
                int height = plane == 0 ? HEIGHT : HEIGHT / 2;
                ByteBuffer buffer = planes[plane].getBuffer();
                int row = planes[plane].getRowStride();
                int pixel = planes[plane].getPixelStride();
                for (int y = 0; y < height; y++) {
                    for (int x = 0; x < width; x++) {
                        buffer.put(y * row + x * pixel, sampleValue(plane, x, y, frame));
                    }
                }
            }
            return size;
        }
        ByteBuffer buffer = codec.getInputBuffer(index);
        if (buffer == null) {
            throw new IllegalStateException("Encoder offered no input buffer");
        }
        buffer.clear();
        for (int y = 0; y < HEIGHT; y++) {
            for (int x = 0; x < WIDTH; x++) {
                buffer.put(sampleValue(0, x, y, frame));
            }
        }
        // Neutral chroma is the same byte for planar (U then V) and semi-planar (UV pairs).
        for (int i = 0; i < WIDTH * HEIGHT / 2; i++) {
            buffer.put(NEUTRAL_CHROMA);
        }
        return size;
    }

    private static byte sampleValue(int plane, int x, int y, int frame) {
        return plane == 0 ? (byte) ((x + y + frame * 8) & 0xff) : NEUTRAL_CHROMA;
    }

    private static int fillTone(MediaCodec codec, int index, int buffer) {
        ByteBuffer input = codec.getInputBuffer(index);
        if (input == null) {
            throw new IllegalStateException("Encoder offered no input buffer");
        }
        input.clear();
        for (int i = 0; i < SAMPLES_PER_BUFFER; i++) {
            double t = (buffer * SAMPLES_PER_BUFFER + i) / (double) SAMPLE_RATE;
            short value = (short) (Math.sin(2 * Math.PI * 440 * t) * 3000);
            input.put((byte) (value & 0xff));
            input.put((byte) ((value >> 8) & 0xff));
        }
        return SAMPLES_PER_BUFFER * 2;
    }

    private static void encode(
        File target,
        MediaFormat format,
        int samples,
        Filler filler,
        Clock clock
    ) throws IOException {
        String mime = format.getString(MediaFormat.KEY_MIME);
        File partial = new File(target.getParentFile(), target.getName() + ".part");
        MediaCodec codec = MediaCodec.createEncoderByType(mime);
        MediaMuxer muxer = null;
        boolean muxing = false;
        boolean complete = false;
        try {
            if (mime.startsWith("video/")) {
                format.setInteger(MediaFormat.KEY_COLOR_FORMAT, colorFormat(codec));
            }
            codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            codec.start();
            muxer = new MediaMuxer(partial.getPath(), MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            int queued = 0;
            boolean inputDone = false;
            int track = -1;
            long deadline = SystemClock.elapsedRealtime() + DEADLINE_MS;
            while (true) {
                if (SystemClock.elapsedRealtime() > deadline) {
                    throw new IOException("Encoding " + target.getName() + " timed out");
                }
                if (!inputDone) {
                    int index = codec.dequeueInputBuffer(TIMEOUT_US);
                    if (index >= 0 && queued == samples) {
                        codec.queueInputBuffer(
                            index, 0, 0, clock.ptsUs(queued), MediaCodec.BUFFER_FLAG_END_OF_STREAM
                        );
                        inputDone = true;
                    } else if (index >= 0) {
                        int size = filler.fill(codec, index, queued);
                        codec.queueInputBuffer(index, 0, size, clock.ptsUs(queued), 0);
                        queued++;
                    }
                }
                int output = codec.dequeueOutputBuffer(info, TIMEOUT_US);
                if (output == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    track = muxer.addTrack(codec.getOutputFormat());
                    muxer.start();
                    muxing = true;
                } else if (output >= 0) {
                    ByteBuffer data = codec.getOutputBuffer(output);
                    boolean config = (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0;
                    if (data != null && muxing && !config && info.size > 0) {
                        data.position(info.offset);
                        data.limit(info.offset + info.size);
                        muxer.writeSampleData(track, data, info);
                    }
                    codec.releaseOutputBuffer(output, false);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        break;
                    }
                }
            }
            codec.stop();
            muxing = false;
            muxer.stop();
            complete = true;
        } finally {
            codec.release();
            if (muxer != null) {
                if (muxing) {
                    try {
                        muxer.stop();
                    } catch (IllegalStateException ignored) {
                        // Nothing usable was written; the partial file is deleted below.
                    }
                }
                muxer.release();
            }
            if (!complete) {
                partial.delete();
            }
        }
        rename(partial, target);
    }

    private static void rename(File from, File to) throws IOException {
        if (!from.renameTo(to)) {
            from.delete();
            throw new IOException("Could not store " + to.getName());
        }
    }
}
