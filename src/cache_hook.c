#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <time.h>

/* Experimental private CoreFSCache ABI, observed on macOS 27.2 (26B123). */
extern int fscache_open_with_key(const char *, const void *, void *, const void *);

void mch_log(const char *fmt, ...) {
    char message[2048], line[2304];
    va_list ap; va_start(ap, fmt);
    vsnprintf(message, sizeof(message), fmt, ap); va_end(ap);
    int len = snprintf(line, sizeof(line), "[metal-cache-hook pid=%d] %s\n", getpid(), message);
    if (len < 0) return;
    size_t n = (size_t)len < sizeof(line) ? (size_t)len : sizeof(line)-1;
    write(STDERR_FILENO, line, n);
    const char *path = getenv("MCH_LOG");
    if (!path || !*path) return;
    int fd = open(path, O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC|O_NOFOLLOW, 0600);
    if (fd < 0) return;
    struct stat st;
    if (!fstat(fd, &st) && S_ISREG(st.st_mode) && st.st_size < 4*1024*1024) write(fd, line, n);
    close(fd);
}

uint64_t mch_budget(void) {
    const char *s = getenv("MCH_CACHE_BYTES");
    if (!s || !*s) return 2147483648ULL;
    char *end; unsigned long long value = strtoull(s, &end, 10);
    /* This build deliberately supports only the tested extension to 2 GiB. */
    if (*end || value < 1073741824ULL || value > 2147483648ULL) return 2147483648ULL;
    return value;
}

static int target_path(const char *path) {
    const char *name = getenv("MCH_TARGET_EXE");
    if (!name || !*name) name = "PBZ.exe";
    if (strchr(name, '/') || strchr(name, '\\')) return 0;
    char prefix[256];
    const char *backends[] = {"d3dm", "mullion"};
    for (unsigned i=0; i<2; ++i) {
        int n = snprintf(prefix, sizeof(prefix), "/%s/%s/", backends[i], name);
        if (n > 0 && (size_t)n < sizeof(prefix) && strstr(path, prefix)) return 1;
    }
    return 0;
}

static int cache_open_hook(const char *path, const void *options, void *out, const void *key) {
    if (path && options && target_path(path)) {
        _Alignas(16) unsigned char copy[16];
        uint64_t old_size;
        memcpy(copy, options, sizeof(copy));
        memcpy(&old_size, copy, 8);
        /* Do not turn an explicitly disabled cache back on. */
        uint64_t budget = mch_budget();
        if (old_size && old_size < budget) {
            memcpy(copy, &budget, 8);
            int result = fscache_open_with_key(path, copy, out, key);
            mch_log("cache-open budget=%llu -> %llu result=%d path=%s",
                (unsigned long long)old_size, (unsigned long long)budget, result, path);
            return result;
        }
        mch_log("cache-open budget unchanged=%llu path=%s", (unsigned long long)old_size, path);
    }
    return fscache_open_with_key(path, options, out, key);
}

__attribute__((used)) static struct {const void *replacement; const void *original;} interpose
__attribute__((section("__DATA,__interpose"))) = {
    (const void *)cache_open_hook, (const void *)fscache_open_with_key
};

__attribute__((constructor)) static void loaded(void) {
    mch_log("loaded v1.0 experimental; budget=%llu; target=%s",
        (unsigned long long)mch_budget(), getenv("MCH_TARGET_EXE") ?: "PBZ.exe");
}
