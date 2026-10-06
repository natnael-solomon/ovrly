// RES-02 whisper.cpp replay driver: loads one ggml model, then transcribes
// each listed 16 kHz mono PCM16 file independently and writes JSON lines
// with raw segments (chunk-relative, whisper 10 ms units converted to ms),
// timings and memory. Raw segment times are never clamped.
//
// usage: whisper_bench <model.bin> <list.tsv> <out.jsonl> [threads] [pace_ms]
#include <unistd.h>

#include "common.h"
#include "whisper.h"

static void quiet(enum ggml_log_level level, const char *text, void *user) {
    (void)level;
    (void)text;
    (void)user;
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: whisper_bench <model.bin> <list.tsv> <out.jsonl> [threads] [pace_ms]\n");
        return 2;
    }
    int threads = argc > 4 ? atoi(argv[4]) : 4;
    long pace = argc > 5 ? strtol(argv[5], NULL, 10) : 0;
    FILE *list = fopen(argv[2], "r");
    FILE *out = fopen(argv[3], "w");
    if (!list || !out) {
        fprintf(stderr, "cannot open list or output\n");
        return 2;
    }
    whisper_log_set(quiet, NULL);
    struct whisper_context_params cparams = whisper_context_default_params();
    cparams.use_gpu = false;
    cparams.flash_attn = false;
    long rss, peak;
    double t0 = now_ms();
    struct whisper_context *ctx = whisper_init_from_file_with_params(argv[1], cparams);
    double init_ms = now_ms() - t0;
    rss_kib(&rss, &peak);
    fprintf(out, "{\"type\":\"init\",\"runtime\":\"whisper.cpp\",\"model\":");
    json_string(out, argv[1]);
    fprintf(out, ",\"system_info\":");
    json_string(out, whisper_print_system_info());
    fprintf(out, ",\"status\":\"%s\",\"init_ms\":%.3f,\"rss_kib\":%ld,\"peak_rss_kib\":%ld,\"threads\":%d,\"pace_ms\":%ld}\n",
            ctx ? "ok" : "ASR_UNAVAILABLE", init_ms, rss, peak, threads, pace);
    fflush(out);
    if (!ctx) return 3;

    struct whisper_full_params p = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
    p.n_threads = threads;
    p.language = "en";
    p.translate = false;
    p.no_context = true;
    p.print_progress = false;
    p.print_realtime = false;
    p.print_timestamps = false;
    p.print_special = false;

    char id[256], path[768];
    double first = -1;
    int index = 0, failures = 0;
    while (next_item(list, id, sizeof id, path, sizeof path)) {
        if (pace > 0) {
            if (first < 0) first = now_ms();
            double due = first + (double)index * (double)pace;
            double wait = due - now_ms();
            if (wait > 0) usleep((useconds_t)(wait * 1000.0));
        }
        index++;
        double start = now_ms();
        pcm16 pcm = read_audio(path);
        fprintf(out, "{\"type\":\"chunk\",\"id\":");
        json_string(out, id);
        fprintf(out, ",\"started_ms\":%.3f", first < 0 ? 0.0 : start - first);
        if (!pcm.samples) {
            failures++;
            fprintf(out, ",\"status\":\"ASR_UNAVAILABLE\",\"error\":");
            json_string(out, pcm.error);
            fprintf(out, ",\"wall_ms\":%.3f}\n", now_ms() - start);
            fflush(out);
            continue;
        }
        float *f = malloc((pcm.count ? pcm.count : 1) * sizeof(float));
        for (size_t i = 0; i < pcm.count; i++) f[i] = (float)pcm.samples[i] / 32768.0f;
        int rc = whisper_full(ctx, p, f, (int)pcm.count);
        double wall = now_ms() - start;
        fprintf(out, ",\"samples\":%zu", pcm.count);
        if (rc != 0) {
            failures++;
            fprintf(out, ",\"status\":\"ASR_UNAVAILABLE\",\"error\":\"whisper_full_%d\",\"wall_ms\":%.3f}\n", rc, wall);
        } else {
            fprintf(out, ",\"segments\":[");
            int n = whisper_full_n_segments(ctx);
            for (int i = 0; i < n; i++) {
                fprintf(out, "%s{\"start_ms\":%lld,\"end_ms\":%lld,\"text\":", i ? "," : "",
                        (long long)whisper_full_get_segment_t0(ctx, i) * 10,
                        (long long)whisper_full_get_segment_t1(ctx, i) * 10);
                json_string(out, whisper_full_get_segment_text(ctx, i));
                fputc('}', out);
            }
            rss_kib(&rss, &peak);
            fprintf(out, "],\"status\":\"ok\",\"wall_ms\":%.3f,\"rss_kib\":%ld,\"peak_rss_kib\":%ld}\n", wall, rss, peak);
        }
        free(f);
        free(pcm.samples);
        fflush(out);
    }
    fprintf(out, "{\"type\":\"end\",\"items\":%d,\"failures\":%d}\n", index, failures);
    fclose(out);
    fclose(list);
    whisper_free(ctx);
    return failures ? 1 : 0;
}
