// Shared helpers for the RES-02 on-device ASR replay drivers.
#ifndef RES02_COMMON_H
#define RES02_COMMON_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1e6;
}

// Current and peak resident set size in KiB, from /proc/self/status.
static void rss_kib(long *current, long *peak) {
    *current = -1;
    *peak = -1;
    FILE *f = fopen("/proc/self/status", "r");
    if (!f) return;
    char line[256];
    while (fgets(line, sizeof line, f)) {
        if (strncmp(line, "VmRSS:", 6) == 0) *current = strtol(line + 6, NULL, 10);
        if (strncmp(line, "VmHWM:", 6) == 0) *peak = strtol(line + 6, NULL, 10);
    }
    fclose(f);
}

static void json_string(FILE *out, const char *s) {
    fputc('"', out);
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p == '"' || *p == '\\') fprintf(out, "\\%c", *p);
        else if (*p == '\n') fputs("\\n", out);
        else if (*p == '\r') fputs("\\r", out);
        else if (*p == '\t') fputs("\\t", out);
        else if (*p < 0x20) fprintf(out, "\\u%04x", *p);
        else fputc(*p, out);
    }
    fputc('"', out);
}

typedef struct {
    int16_t *samples;
    size_t count;
    char error[160];
} pcm16;

static uint32_t le32(const unsigned char *b) {
    return (uint32_t)b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16 | (uint32_t)b[3] << 24;
}

// Reads 16 kHz mono signed 16-bit little-endian samples. A RIFF/WAVE file is
// parsed and only its data chunk is returned, so header bytes are never fed
// as samples. Any other file is treated as raw headerless PCM (chunk ZIP
// audio-16000-mono-s16le.pcm).
static pcm16 read_audio(const char *path) {
    pcm16 r = {0};
    FILE *f = fopen(path, "rb");
    if (!f) {
        snprintf(r.error, sizeof r.error, "open_failed");
        return r;
    }
    unsigned char h[12];
    size_t got = fread(h, 1, 12, f);
    if (got < 12 || memcmp(h, "RIFF", 4) || memcmp(h + 8, "WAVE", 4)) {
        fseek(f, 0, SEEK_END);
        long size = ftell(f);
        fseek(f, 0, SEEK_SET);
        if (size < 0 || size % 2) {
            snprintf(r.error, sizeof r.error, "raw_pcm_odd_length");
        } else {
            r.samples = malloc(size ? (size_t)size : 2);
            if (!r.samples || fread(r.samples, 1, (size_t)size, f) != (size_t)size) {
                snprintf(r.error, sizeof r.error, "short_raw_pcm");
                free(r.samples);
                r.samples = NULL;
            } else {
                r.count = (size_t)size / 2;
            }
        }
        fclose(f);
        if (r.samples && r.count == 0) {
            free(r.samples);
            r.samples = NULL;
            snprintf(r.error, sizeof r.error, "no_samples");
        }
        return r;
    }
    int fmt_ok = 0;
    for (;;) {
        unsigned char ch[8];
        if (fread(ch, 1, 8, f) != 8) {
            snprintf(r.error, sizeof r.error, "no_data_chunk");
            break;
        }
        uint32_t size = le32(ch + 4);
        if (!memcmp(ch, "fmt ", 4)) {
            unsigned char fmt[40];
            if (size < 16 || size > sizeof fmt || fread(fmt, 1, size, f) != size) {
                snprintf(r.error, sizeof r.error, "bad_fmt_chunk");
                break;
            }
            unsigned format = fmt[0] | fmt[1] << 8, channels = fmt[2] | fmt[3] << 8;
            uint32_t rate = le32(fmt + 4);
            unsigned bits = fmt[14] | fmt[15] << 8;
            if (format != 1 || channels != 1 || rate != 16000 || bits != 16) {
                snprintf(r.error, sizeof r.error, "unsupported_format_%u_%u_%u_%u", format, channels,
                         (unsigned)rate, bits);
                break;
            }
            fmt_ok = 1;
            if (size & 1) fseek(f, 1, SEEK_CUR);
        } else if (!memcmp(ch, "data", 4)) {
            if (!fmt_ok || size % 2) {
                snprintf(r.error, sizeof r.error, "data_before_fmt_or_odd");
                break;
            }
            r.samples = malloc(size ? size : 2);
            if (!r.samples || fread(r.samples, 1, size, f) != size) {
                snprintf(r.error, sizeof r.error, "short_data");
                free(r.samples);
                r.samples = NULL;
                break;
            }
            r.count = size / 2;
            break;
        } else {
            fseek(f, size + (size & 1), SEEK_CUR);
        }
    }
    fclose(f);
    if (r.samples && r.count == 0) {
        free(r.samples);
        r.samples = NULL;
        snprintf(r.error, sizeof r.error, "no_samples");
    }
    return r;
}

// Reads "id<TAB>path" lines. Returns 0 at end of file.
static int next_item(FILE *list, char *id, size_t id_len, char *path, size_t path_len) {
    char line[1024];
    while (fgets(line, sizeof line, list)) {
        line[strcspn(line, "\r\n")] = 0;
        char *tab = strchr(line, '\t');
        if (!tab || tab == line) continue;
        *tab = 0;
        snprintf(id, id_len, "%s", line);
        snprintf(path, path_len, "%s", tab + 1);
        return 1;
    }
    return 0;
}

#endif
