#define _GNU_SOURCE

#include <errno.h>
#include <limits.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void usage(const char *program)
{
    fprintf(stderr, "Usage: %s <cpu> <program> [args...]\n", program);
}

static int parse_cpu(const char *text, int *cpu)
{
    char *end = NULL;
    long value;

    errno = 0;
    value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value < 0 ||
        value >= CPU_SETSIZE || value > INT_MAX) {
        return -1;
    }

    *cpu = (int)value;
    return 0;
}

static int pin_current_process(int cpu)
{
    cpu_set_t requested;
    cpu_set_t observed;

    CPU_ZERO(&requested);
    CPU_SET(cpu, &requested);
    if (sched_setaffinity(0, sizeof(requested), &requested) != 0) {
        fprintf(stderr, "sched_setaffinity(cpu=%d) failed: %s\n", cpu,
                strerror(errno));
        return -1;
    }

    CPU_ZERO(&observed);
    if (sched_getaffinity(0, sizeof(observed), &observed) != 0) {
        fprintf(stderr, "sched_getaffinity failed: %s\n", strerror(errno));
        return -1;
    }
    if (CPU_COUNT(&observed) != 1 || !CPU_ISSET(cpu, &observed)) {
        fprintf(stderr,
                "affinity round-trip mismatch: requested cpu=%d, observed count=%d\n",
                cpu, CPU_COUNT(&observed));
        return -1;
    }

    printf("TASK1_TOPOLOGY_AFFINITY cpu=%d verified=true\n", cpu);
    fflush(stdout);
    return 0;
}

int main(int argc, char **argv)
{
    int cpu;

    if (argc < 3) {
        usage(argv[0]);
        return 2;
    }
    if (parse_cpu(argv[1], &cpu) != 0) {
        fprintf(stderr, "invalid CPU ID: %s\n", argv[1]);
        return 2;
    }
    if (argv[2][0] != '/') {
        fprintf(stderr, "program path must be absolute: %s\n", argv[2]);
        return 2;
    }
    if (pin_current_process(cpu) != 0) {
        return 1;
    }

    execv(argv[2], &argv[2]);
    fprintf(stderr, "execv(%s) failed: %s\n", argv[2], strerror(errno));
    return 1;
}
