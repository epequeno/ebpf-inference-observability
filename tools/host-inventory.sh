#!/usr/bin/env bash
# host-inventory.sh — read-only reconnaissance for the ebpf-inference-observability
# project. Changes nothing, installs nothing.
#
#   ./tools/host-inventory.sh                    # print to stdout
#   ./tools/host-inventory.sh > inventory/report.md
#   sudo ./tools/host-inventory.sh               # adds the root-only checks
#
# Everything is best-effort: missing commands and unreadable files are reported,
# never fatal. Safe to run on a box you care about.

set -u
export LC_ALL=C

SUDO=""
[ "$(id -u)" -eq 0 ] && SUDO="yes"

have() { command -v "$1" >/dev/null 2>&1; }

sec() { printf '\n## %s\n\n' "$1"; }
kv()  { printf '| %s | %s |\n' "$1" "${2:-—}"; }
run() { # run a command, print its output indented, tolerate failure
  local out; out=$("$@" 2>&1) || true
  printf '```\n%s\n```\n' "${out:-<no output>}"
}
exist() { [ -e "$1" ] && echo "yes" || echo "**no**"; }

printf '# Host inventory — %s\n\n' "$(hostname 2>/dev/null || echo unknown)"
printf 'Collected: %s  ·  via `%s`  ·  root: %s\n' \
  "$(date -Is 2>/dev/null)" "$0" "${SUDO:-no}"
echo
echo '| field | value |'
echo '|---|---|'

# ---------------------------------------------------------------- 1. OS / kernel
sec '1. OS and kernel'
kv 'hostname'      "$(hostname 2>/dev/null)"
kv 'uname -r'      "$(uname -r 2>/dev/null)"
kv 'uname -m'      "$(uname -m 2>/dev/null)"
kv 'distro'        "$(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}")"
kv 'uptime'        "$(uptime -p 2>/dev/null || uptime 2>/dev/null)"
kv 'virtualisation' "$(systemd-detect-virt 2>/dev/null || echo 'not systemd / unknown')"
kv 'secure boot'   "$(if have mokutil; then mokutil --sb-state 2>/dev/null || echo 'needs root'; else echo 'mokutil absent'; fi)"
echo '> Bare metal or a VM matters: nested virtualisation can hide kernel features. Secure boot + kernel lockdown can block kprobes entirely.'

# ------------------------------------------------------- 2. eBPF prerequisites
sec '2. eBPF prerequisites'
kv 'BTF /sys/kernel/btf/vmlinux' "$(if [ -e /sys/kernel/btf/vmlinux ]; then echo "yes ($(wc -c </sys/kernel/btf/vmlinux) bytes)"; else echo '**NO — CO-RE and bpftrace will not work**'; fi)"
kv '/proc/kallsyms symbols'      "$([ -r /proc/kallsyms ] && wc -l </proc/kallsyms || echo 'unreadable')"
kv 'tcp_sendmsg in kallsyms'     "$(grep -c ' tcp_sendmsg$' /proc/kallsyms 2>/dev/null || echo 0)"
kv 'tcp_recvmsg in kallsyms'     "$(grep -c ' tcp_recvmsg$' /proc/kallsyms 2>/dev/null || echo 0)"
kv 'tracefs mounted'             "$([ -r /proc/mounts ] && grep -q tracefs /proc/mounts && echo yes || echo 'no')"
kv 'tracefs mountable at /sys/kernel/tracing' "$([ -e /sys/kernel/tracing/kprobe_events ] && echo yes || echo no)"
kv 'kprobe_events'               "$([ -e /sys/kernel/tracing/kprobe_events ] && echo yes || echo no)"
kv 'uprobe_events'               "$([ -e /sys/kernel/tracing/uprobe_events ] && echo yes || echo no)"
kv 'sched tracepoints'           "$(ls /sys/kernel/tracing/events/sched/ 2>/dev/null | wc -l)"
kv 'net tracepoints'             "$(ls /sys/kernel/tracing/events/net/ 2>/dev/null | wc -l)"
kv 'kmem tracepoints'            "$(ls /sys/kernel/tracing/events/kmem/ 2>/dev/null | wc -l)"
kv 'syscalls tracepoints'        "$(ls /sys/kernel/tracing/events/syscalls/ 2>/dev/null | wc -l)"
kv 'kernel.perf_event_paranoid'  "$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo '?')"
kv 'kernel.unprivileged_bpf_disabled' "$(cat /proc/sys/kernel/unprivileged_bpf_disabled 2>/dev/null || echo '?')"
kv 'kernel lockdown'             "$(cat /sys/kernel/security/lockdown 2>/dev/null || echo 'not readable (needs root) / absent')"

echo
echo 'Key kernel config flags:'
cfg="/boot/config-$(uname -r)"
if [ -r "$cfg" ]; then
  echo '```'
  for f in BPF_SYSCALL BPF_JIT BPF_EVENTS KPROBES KPROBE_EVENTS UPROBE_EVENTS FTRACE FUNCTION_TRACER DEBUG_INFO_BTF; do
    grep -E "^(CONFIG_${f}=|# CONFIG_${f} is not set)" "$cfg" 2>/dev/null || echo "CONFIG_${f}: <absent>"
  done
  echo '```'
else
  echo "_($cfg not readable — /proc/config.gz or the distro's config package would have it)_"
  if [ -r /proc/config.gz ]; then
    zcat /proc/config.gz 2>/dev/null | grep -E 'CONFIG_(BPF_SYSCALL|KPROBES|DEBUG_INFO_BTF)=' || true
  fi
fi

# ------------------------------------------------------------ 3. toolchain
sec '3. eBPF toolchain'
kv 'bpftrace'   "$(bpftrace --version 2>/dev/null || echo '**absent**')"
kv 'bpftool'    "$(bpftool version 2>/dev/null | head -1 || echo absent)"
kv 'bcc-tools'  "$(ls /usr/share/bcc/tools 2>/dev/null | wc -l) tools in /usr/share/bcc/tools"
kv 'clang'      "$(clang --version 2>/dev/null | head -1 || echo absent)"
kv 'llvm-strip' "$(llvm-strip --version 2>/dev/null | head -1 || echo absent)"
kv 'libbpf'     "$(ldconfig -p 2>/dev/null | grep -c libbpf)"
kv 'gcc'        "$(gcc --version 2>/dev/null | head -1 || echo absent)"
kv 'go'         "$(go version 2>/dev/null || echo absent)"
kv 'rustc'      "$(rustc --version 2>/dev/null || echo absent)"
kv 'python3'    "$(python3 --version 2>/dev/null || echo absent)"
kv 'uv'         "$(uv --version 2>/dev/null || echo absent)"
kv 'docker'     "$(docker --version 2>/dev/null || echo absent)"
kv 'headers'    "$([ -d "/lib/modules/$(uname -r)/build" ] && echo "yes ($(readlink -f "/lib/modules/$(uname -r)/build" 2>/dev/null))" || echo 'no (kernel headers/module build tree absent)')"

# --------------------------------------------------------------- 4. GPU
sec '4. GPU'
if have nvidia-smi; then
  kv 'vendor' 'NVIDIA (nvidia-smi present)'
  echo
  echo '```'
  nvidia-smi 2>&1 | head -15
  echo '```'
  echo
  echo 'Full query (name, memory, compute capability, driver, CUDA):'
  echo '```'
  nvidia-smi --query-gpu=index,name,memory.total,memory.used,compute_cap,driver_version,vbios_version \
             --format=csv 2>&1 | head -12
  echo '```'
  kv 'CUDA driver (nvidia-smi)' "$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)"
  kv 'CUDA runtime (nvcc)'     "$(nvcc --version 2>/dev/null | grep -o 'release [0-9.]*' || echo 'nvcc absent')"
  kv 'cuLaunchKernel symbol'   "$(for p in $(ldconfig -p 2>/dev/null | awk '/libcuda\.so/{print $NF}'); do nm -D --defined-only "$p" 2>/dev/null | grep -qw cuLaunchKernel && echo "PRESENT in $p" && break; done | head -1)"
else
  kv 'vendor' 'no nvidia-smi'
fi
echo
echo 'libcuda / libcudart libraries found:'
echo '```'
ldconfig -p 2>/dev/null | grep -E 'libcuda\.so|libcudart\.so|libnvidia-ml' | head -8 || echo '<none>'
echo '```'
echo
echo 'CUDA symbols central to Phase 3 (uprobe targets):'
echo '```'
for p in $(ldconfig -p 2>/dev/null | awk '/libcuda\.so|libcudart\.so/{print $NF}' | sort -u); do
  [ -r "$p" ] || continue
  printf '%s:\n' "$p"
  nm -D --defined-only "$p" 2>/dev/null \
    | grep -oE 'cu(LaunchKernel|LaunchKernelEx|MemAlloc|MemFree|MemcpyHtoD|MemcpyDtoH|GraphLaunch)[A-Za-z]*' \
    | sort -u | sed 's/^/  /' | head -12
done
echo '```'
if have rocm-smi; then
  echo '**ROCm also present:**'; run rocm-smi --showproductname
fi
echo
echo 'All graphics devices (lspci):'
echo '```'
(lspci 2>/dev/null | grep -iE 'vga|3d|display' || echo '<lspci absent or no display devices>')
echo '```'

# ------------------------------------------------------- 5. CPU / RAM / disk
sec '5. CPU, memory, disk'
kv 'CPU model'  "$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ //' || echo '?')"
kv 'CPU cores'  "$(nproc 2>/dev/null)"
kv 'CPU phys'   "$(grep -c '^processor' /proc/cpuinfo 2>/dev/null)"
kv 'NUMA nodes' "$(ls -d /sys/devices/system/node/node* 2>/dev/null | wc -l)"
kv 'RAM total'  "$(free -h 2>/dev/null | awk '/^Mem:/{print $2}' || echo '?')"
kv 'RAM avail'  "$(free -h 2>/dev/null | awk '/^Mem:/{print $7}' || echo '?')"
kv 'swap'       "$(free -h 2>/dev/null | awk '/^Swap:/{print $2}' || echo '?')"
kv 'hugepages'  "$(grep -i HugePages_Total /proc/meminfo 2>/dev/null || echo '?')"
echo
echo 'Filesystems (model storage matters):'
echo '```'
df -h -x tmpfs -x devtmpfs -x squashfs 2>/dev/null | head -12
echo '```'

# ------------------------------------------------------- 6. inference stack
sec '6. Existing inference stack'
kv 'llama.cpp'      "$(ls -d ~/llama.cpp /opt/llama.cpp /usr/local/llama.cpp 2>/dev/null | head -1 || echo 'not found in usual places')"
kv 'llama-server'   "$(command -v llama-server || command -v llama-cli || echo absent)"
kv 'vllm'           "$(python3 -c 'import vllm; print(vllm.__version__)' 2>/dev/null || echo absent)"
kv 'torch'          "$(python3 -c 'import torch; print(torch.__version__, torch.cuda.is_available())' 2>/dev/null || echo absent)"
kv 'transformers'   "$(python3 -c 'import transformers; print(transformers.__version__)' 2>/dev/null || echo absent)"
kv 'huggingface cache' "$(du -sh ~/.cache/huggingface 2>/dev/null | cut -f1 || echo absent)"
echo
echo 'GGUF / model files already on disk (top 10 by size):'
echo '```'
find ~ /opt /srv -maxdepth 4 \( -name '*.gguf' -o -name '*.safetensors' \) 2>/dev/null | head -10 || echo '<none found>'
echo '```'

# ---------------------------------------------------------------- 7. network
sec '7. Network'
kv 'hostname -I' "$(hostname -I 2>/dev/null || echo '?')"
kv 'default route' "$(ip route show default 2>/dev/null | head -1 || echo '?')"
kv 'internet reachable' "$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://huggingface.co 2>/dev/null || echo 'no (blocked? offline?)')"
kv 'listening ports' "$( (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | awk 'NR>1{print $4}' | head -8 | tr '\n' ' ')"

# ------------------------------------------------------------ 8. root-only
sec '8. Root-only checks'
if [ -n "$SUDO" ]; then
  kv 'lockdown'        "$(cat /sys/kernel/security/lockdown 2>/dev/null || echo absent)"
  kv 'dmesg bpf lines' "$(dmesg 2>/dev/null | grep -ic bpf)"
  kv 'tracefs contents' "$(ls /sys/kernel/tracing 2>/dev/null | wc -l) entries"
else
  echo 'Not root — re-run with `sudo` for: lockdown state, tracefs contents, dmesg.'
fi

# --------------------------------------------------------------- 9. verdict
sec '9. Readiness verdict'

btf=$([ -e /sys/kernel/btf/vmlinux ] && echo yes || echo no)
tracefs=$([ -e /sys/kernel/tracing/kprobe_events ] && echo yes || echo no)
lockdown="$(cat /sys/kernel/security/lockdown 2>/dev/null || echo unknown)"
kv 'eBPF usable (BTF + kprobe_events)' "$([ "$btf" = yes ] && [ "$tracefs" = yes ] && echo 'YES' || echo 'NO — see section 2')"
case "$lockdown" in
  *"[none]"*|unknown) kv 'lockdown' "$lockdown (kprobes expected to work)" ;;
  *) kv 'lockdown' "$lockdown — **kprobes may be blocked**" ;;
esac
kv 'bpftrace installed' "$(have bpftrace && echo yes || echo 'no — install with the distro package')"

if have nvidia-smi; then
  vram=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
  cap=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1)
  kv 'GPU' "$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1) (${vram:-?} MiB, compute ${cap:-?})"
  kv 'Phase 3 (CUDA uprobes) viable' "$([ -n "${vram:-}" ] && echo yes || echo 'unknown')"
  if [ -n "${vram:-}" ] && [ "$vram" -ge 12000 ] 2>/dev/null; then
    kv '1–3B model in VRAM' 'yes comfortably'
  elif [ -n "${vram:-}" ]; then
    kv '1–3B model in VRAM' "tight — ${vram} MiB; quantise (Q4/Q5) or use a smaller model"
  fi
else
  kv 'GPU' 'none detected — Phase 3 needs an NVIDIA GPU; Phases 1/2/4 still work on CPU'
fi

printf '\n---\n_Generated by tools/host-inventory.sh. Read-only: nothing on this host was modified._\n'
