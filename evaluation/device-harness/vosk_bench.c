// RES-02 Vosk replay driver: loads one model, then recognizes each listed
// 16 kHz mono PCM16 file independently (chunk-relative times) and writes
// JSON lines with raw recognizer output, timings and memory.
//
// usage: vosk_bench <model_dir> <list.tsv> <out.jsonl> [pace_ms]
// list.tsv lines: <id>\t<audio path>. pace_ms > 0 starts item i no earlier
// than i * pace_ms after the first item (live-capture pacing).
#include <unistd.h>

#include "common.h"
#include "vosk_api.h"

#define BLOCK_SAMPLES 4000

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: vosk_bench <model_dir> <list.tsv> <out.jsonl> [pace_ms]\n");
        return 2;
    }
    long pace = argc > 4 ? strtol(argv[4], NULL, 10) : 0;
    FILE *list = fopen(argv[2], "r");
    FILE *out = fopen(argv[3], "w");
    if (!list || !out) {
        fprintf(stderr, "cannot open list or output\n");
        return 2;
    }
    vosk_set_log_level(-1);
    long rss, peak;
    double t0 = now_ms();
    VoskModel *model = vosk_model_new(argv[1]);
    double init_ms = now_ms() - t0;
    rss_kib(&rss, &peak);
    fprintf(out, "{\"type\":\"init\",\"runtime\":\"vosk\",\"model_dir\":");
    json_string(out, argv[1]);
    fprintf(out, ",\"status\":\"%s\",\"init_ms\":%.3f,\"rss_kib\":%ld,\"peak_rss_kib\":%ld,\"pace_ms\":%ld}\n",
            model ? "ok" : "ASR_UNAVAILABLE", init_ms, rss, peak, pace);
    fflush(out);
    if (!model) return 3;

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
        double r0 = now_ms();
        VoskRecognizer *rec = vosk_recognizer_new(model, 16000.0f);
        double rec_ms = now_ms() - r0;
        if (!rec) {
            failures++;
            fprintf(out, ",\"samples\":%zu,\"status\":\"ASR_UNAVAILABLE\",\"error\":\"recognizer_new_failed\",\"wall_ms\":%.3f}\n",
                    pcm.count, now_ms() - start);
            free(pcm.samples);
            fflush(out);
            continue;
        }
        vosk_recognizer_set_words(rec, 1);
        fprintf(out, ",\"samples\":%zu,\"recognizer_new_ms\":%.3f,\"results\":[", pcm.count, rec_ms);
        int emitted = 0;
        for (size_t at = 0; at < pcm.count; at += BLOCK_SAMPLES) {
            size_t n = pcm.count - at < BLOCK_SAMPLES ? pcm.count - at : BLOCK_SAMPLES;
            if (vosk_recognizer_accept_waveform(rec, (const char *)(pcm.samples + at), (int)(n * 2)) > 0) {
                fprintf(out, "%s%s", emitted++ ? "," : "", vosk_recognizer_result(rec));
            }
        }
        fprintf(out, "%s%s]", emitted ? "," : "", vosk_recognizer_final_result(rec));
        double wall = now_ms() - start;
        vosk_recognizer_free(rec);
        free(pcm.samples);
        rss_kib(&rss, &peak);
        fprintf(out, ",\"status\":\"ok\",\"wall_ms\":%.3f,\"rss_kib\":%ld,\"peak_rss_kib\":%ld}\n", wall, rss, peak);
        fflush(out);
    }
    fprintf(out, "{\"type\":\"end\",\"items\":%d,\"failures\":%d}\n", index, failures);
    fclose(out);
    fclose(list);
    vosk_model_free(model);
    return failures ? 1 : 0;
}
