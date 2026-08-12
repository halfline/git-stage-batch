#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only

set -euo pipefail

repo_dir=${1:-$HOME/castkms}
expected_release=${2:?missing expected kernel release}
result_dir=$repo_dir/test-results/vm-smoke
stock_loaded=0
cast_loaded=0
configfs_dev=/sys/kernel/config/castkms/lifetime-test
runtime_dir=
unplug_gate_open=0
unplug_helper_pid=
mode_gate_open=0
mode_holder_pid=
crc_fd=
crc_pid=

append_crc_record()
{
	local destination=$1
	local context=$2
	local line

	if ! IFS= read -r -t 2 -u "$crc_fd" line; then
		printf 'CRC capture stopped during %s\n' "$context" >&2
		return 1
	fi
	if [[ ! $line =~ ^0x[[:xdigit:]]{8}\ 0x[[:xdigit:]]{8}$ ]]; then
		printf 'malformed CRC record during %s: %s\n' "$context" "$line" >&2
		return 1
	fi
	printf '%s\n' "$line" >> "$destination"
}

run_writeback()
{
	local label=$1
	local output=$result_dir/writeback-$label.raw
	local log=$result_dir/writeback-$label.txt
	local status=0

	sudo rm -f "$output"
	sudo timeout --signal=TERM --kill-after=2s 8s \
		modetest -a -M castkms \
		-s "$virtual_connector,$writeback_connector@$crtc_id:1024x768" \
		-P "$plane_id@$crtc_id:1024x768@XR24" \
		-o "$output" -d </dev/null > "$log" 2>&1 || status=$?
	if test "$status" -ne 0 ||
		! grep -F 'Dumping buffer' "$log" >/dev/null ||
		grep -F 'Poll for writeback error:' "$log" >/dev/null ||
		grep -F 'Atomic Commit failed [1]' "$log" >/dev/null; then
		cat "$log" >&2
		return 1
	fi
	if test "$(stat -c %s "$output")" -ne $((1024 * 768 * 4)); then
		printf 'writeback %s has the wrong size\n' "$label" >&2
		return 1
	fi
	if ! od -An -v -tu1 "$output" | awk '
		{
			for (i = 1; i <= NF; i++) {
				if (!seen) {
					first = $i
					seen = 1
				} else if ($i != first) {
					varied = 1
				}
			}
		}
		END { exit !(seen && varied) }
	'; then
		printf 'writeback %s contains only one byte value\n' "$label" >&2
		return 1
	fi
}

cleanup()
{
	if test -d "$configfs_dev"; then
		sudo rm -f \
			"$configfs_dev/planes/primary-0/possible_crtcs/crtc-0" \
			"$configfs_dev/encoders/encoder-0/possible_crtcs/crtc-0" \
			"$configfs_dev/connectors/connector-0/possible_encoders/encoder-0"
		sudo rmdir "$configfs_dev/connectors/connector-0" 2>/dev/null || true
		sudo rmdir "$configfs_dev/encoders/encoder-0" 2>/dev/null || true
		sudo rmdir "$configfs_dev/planes/primary-0" 2>/dev/null || true
		sudo rmdir "$configfs_dev/crtcs/crtc-0" 2>/dev/null || true
		sudo rmdir "$configfs_dev" 2>/dev/null || true
	fi
	if test -n "$crc_fd"; then
		exec {crc_fd}<&-
		crc_fd=
	fi
	if test -n "$crc_pid"; then
		kill "$crc_pid" 2>/dev/null || true
		wait "$crc_pid" 2>/dev/null || true
	fi
	if test "$mode_gate_open" -eq 1; then
		printf '\n' >&8 2>/dev/null || true
		exec 8>&-
	fi
	if test -n "$mode_holder_pid"; then
		kill "$mode_holder_pid" 2>/dev/null || true
		wait "$mode_holder_pid" 2>/dev/null || true
	fi
	if test "$unplug_gate_open" -eq 1; then
		printf 'x' >&7 2>/dev/null || true
		exec 7>&-
	fi
	if test -n "$unplug_helper_pid"; then
		kill "$unplug_helper_pid" 2>/dev/null || true
		wait "$unplug_helper_pid" 2>/dev/null || true
	fi
	if test -n "$runtime_dir"; then
		rm -f "$runtime_dir/drm-unplug-check" \
			"$runtime_dir/unplug-gate" "$runtime_dir/mode-gate"
		rmdir "$runtime_dir" 2>/dev/null || true
	fi
	if test "$cast_loaded" -eq 1; then
		sudo rmmod castkms || true
	fi
	if test "$stock_loaded" -eq 1; then
		sudo rmmod vkms || true
	fi
}

trap cleanup EXIT

mkdir -p "$result_dir"
cd "$repo_dir"
runtime_dir=$(mktemp -d)

running_release=$(uname -r)
test "$running_release" = "$expected_release"
printf 'kernel=%s\n' "$running_release" | tee "$result_dir/summary.txt"

make clean
test ! -e ./castkms.ko
test ! -e ./src/tests/castkms-kunit-tests.ko
make kunit W=1 2>&1 | tee "$result_dir/build.log"

test "$(modinfo -F name ./castkms.ko)" = castkms
case "$(modinfo -F vermagic ./castkms.ko)" in
	"$expected_release "*) ;;
	*) printf '%s\n' 'module vermagic does not match the guest kernel' >&2; exit 1 ;;
esac

test "$(modinfo -F name ./src/tests/castkms-kunit-tests.ko)" = \
	castkms_kunit_tests
case "$(modinfo -F vermagic ./src/tests/castkms-kunit-tests.ko)" in
	"$expected_release "*) ;;
	*) printf '%s\n' 'KUnit module vermagic does not match the guest kernel' >&2; exit 1 ;;
esac
case ",$(modinfo -F depends ./src/tests/castkms-kunit-tests.ko)," in
	*,castkms,*kunit,*|*,kunit,*castkms,*) ;;
	*) printf '%s\n' 'KUnit module dependencies are incomplete' >&2; exit 1 ;;
esac
printf '%s\n' 'kunit_build=pass' | tee -a "$result_dir/summary.txt"

if ! strings ./castkms.ko > "$result_dir/module-strings.txt"; then
	printf '%s\n' 'could not inspect the module string table' >&2
	exit 1
fi
if grep -i vkms "$result_dir/module-strings.txt" \
		> "$result_dir/legacy-strings.txt"; then
	printf '%s\n' 'legacy VKMS identity remains in castkms.ko' >&2
	exit 1
fi

if awk '{ print $2 }' Module.symvers | grep -Ev '^castkms_' > "$result_dir/non-castkms-exports.txt"; then
	printf '%s\n' 'module contains non-namespaced exports' >&2
	exit 1
fi

if lsmod | grep -Eq '^(vkms|castkms)\b'; then
	printf '%s\n' 'refusing to disturb a pre-existing vkms or castkms module' >&2
	exit 1
fi

if ! mountpoint -q /sys/kernel/config; then
	sudo mount -t configfs none /sys/kernel/config
fi
if ! mountpoint -q /sys/kernel/debug; then
	sudo mount -t debugfs none /sys/kernel/debug
fi

sudo modprobe vkms create_default_dev=0
stock_loaded=1
sudo insmod ./castkms.ko create_default_dev=0
cast_loaded=1

test -d /sys/kernel/config/vkms
test -d /sys/kernel/config/castkms
lsmod | grep -E '^(vkms|castkms)\b' | tee "$result_dir/coexistence-modules.txt"
ls -ld /sys/kernel/config/vkms /sys/kernel/config/castkms | \
	tee "$result_dir/coexistence-configfs.txt"

sudo mkdir "$configfs_dev"
sudo mkdir "$configfs_dev/planes/primary-0"
sudo mkdir "$configfs_dev/crtcs/crtc-0"
sudo mkdir "$configfs_dev/encoders/encoder-0"
sudo mkdir "$configfs_dev/connectors/connector-0"
printf '1\n' | sudo tee "$configfs_dev/planes/primary-0/type" >/dev/null
sudo ln -s "$configfs_dev/crtcs/crtc-0" \
	"$configfs_dev/planes/primary-0/possible_crtcs/crtc-0"
sudo ln -s "$configfs_dev/crtcs/crtc-0" \
	"$configfs_dev/encoders/encoder-0/possible_crtcs/crtc-0"
sudo ln -s "$configfs_dev/encoders/encoder-0" \
	"$configfs_dev/connectors/connector-0/possible_encoders/encoder-0"
printf '1\n' | sudo tee "$configfs_dev/enabled" >/dev/null
test "$(sudo cat "$configfs_dev/enabled")" = 1
sudo udevadm settle

lifetime_debugfs=/sys/kernel/debug/dri/lifetime-test
sudo test -r "$lifetime_debugfs/castkms_config"
lifetime_minor=$(sudo find /sys/kernel/debug/dri -maxdepth 1 -type l \
	-lname lifetime-test -printf '%f\n')
test -n "$lifetime_minor"
lifetime_drm=/dev/dri/card$lifetime_minor
test -c "$lifetime_drm"

cc -std=gnu11 -O2 -Wall -Wextra -Werror \
	-o "$runtime_dir/drm-unplug-check" \
	"$repo_dir/scripts/vm/drm-unplug-check.c"
mkfifo "$runtime_dir/unplug-gate"
exec 7<> "$runtime_dir/unplug-gate"
unplug_gate_open=1

# Keep both userspace-facing descriptors open until the complete configfs
# object has been removed, then require both interfaces to report unplugging.
sudo timeout --signal=TERM --kill-after=2s 15s \
	"$runtime_dir/drm-unplug-check" \
	"$lifetime_drm" "$lifetime_debugfs/castkms_config" \
	<&7 > "$result_dir/configfs-open-fd.txt" 2>&1 &
unplug_helper_pid=$!
for attempt in $(seq 1 50); do
	if grep -Fx 'ready=castkms' "$result_dir/configfs-open-fd.txt" \
			>/dev/null; then
		break
	fi
	if ! kill -0 "$unplug_helper_pid" 2>/dev/null; then
		cat "$result_dir/configfs-open-fd.txt" >&2
		exit 1
	fi
	sleep 0.1
done
if ! grep -Fx 'ready=castkms' "$result_dir/configfs-open-fd.txt" \
		>/dev/null; then
	cat "$result_dir/configfs-open-fd.txt" >&2
	exit 1
fi

# Removing topology cannot be rejected by configfs. It must first disable the
# live DRM device so no runtime object retains the detached configuration.
sudo rm "$configfs_dev/planes/primary-0/possible_crtcs/crtc-0"
test "$(sudo cat "$configfs_dev/enabled")" = 0
printf '%s\n' 'configfs_topology_lifetime=pass' | tee -a "$result_dir/summary.txt"

sudo rm "$configfs_dev/encoders/encoder-0/possible_crtcs/crtc-0"
sudo rm "$configfs_dev/connectors/connector-0/possible_encoders/encoder-0"
sudo rmdir "$configfs_dev/connectors/connector-0"
sudo rmdir "$configfs_dev/encoders/encoder-0"
sudo rmdir "$configfs_dev/planes/primary-0"
sudo rmdir "$configfs_dev/crtcs/crtc-0"
sudo rmdir "$configfs_dev"
printf 'x' >&7
exec 7>&-
unplug_gate_open=0
if ! wait "$unplug_helper_pid"; then
	cat "$result_dir/configfs-open-fd.txt" >&2
	exit 1
fi
unplug_helper_pid=
grep -Fx 'drm_ioctl_after_unplug=ENODEV' \
	"$result_dir/configfs-open-fd.txt" >/dev/null
grep -Fx 'debugfs_read_after_unplug=EIO' \
	"$result_dir/configfs-open-fd.txt" >/dev/null
printf '%s\n' 'configfs_open_fd_lifetime=pass' | tee -a "$result_dir/summary.txt"

sudo rmmod castkms
cast_loaded=0
sudo rmmod vkms
stock_loaded=0

sudo insmod ./castkms.ko \
	create_default_dev=1 \
	enable_cursor=0 \
	enable_overlay=0 \
	enable_writeback=1 \
	enable_plane_pipeline=1
cast_loaded=1

sudo udevadm settle
ls -l /dev/dri | tee "$result_dir/dev-dri.txt"
if ! sudo modetest -a -M castkms -c -p -e \
		> "$result_dir/modetest.txt" 2>&1; then
	cat "$result_dir/modetest.txt" >&2
	exit 1
fi

virtual_connector=$(awk '
	$1 ~ /^[0-9]+$/ && $4 ~ /^Virtual-/ { print $4; exit }
' "$result_dir/modetest.txt")
writeback_connector=$(awk '
	$1 ~ /^[0-9]+$/ && $4 ~ /^Writeback-/ { print $4; exit }
' "$result_dir/modetest.txt")
crtc_id=$(awk '
	$0 == "CRTCs:" { in_crtcs = 1; next }
	in_crtcs && $1 ~ /^[0-9]+$/ { print $1; exit }
' "$result_dir/modetest.txt")
plane_id=$(awk '
	$0 == "Planes:" { in_planes = 1; next }
	in_planes && $1 ~ /^[0-9]+$/ { print $1; exit }
' "$result_dir/modetest.txt")
test -n "$virtual_connector"
test -n "$writeback_connector"
test -n "$crtc_id"
test -n "$plane_id"

# Keep stdin open so noninteractive SSH does not end the flip loop immediately.
page_flip_input_dir=$(mktemp -d)
mkfifo "$page_flip_input_dir/input"
exec 3<> "$page_flip_input_dir/input"
rm "$page_flip_input_dir/input"
rmdir "$page_flip_input_dir"

page_flip_status=0
sudo timeout --signal=INT --kill-after=2s 5s \
	stdbuf --output=L --error=L modetest -M castkms -r -v \
	<&3 > "$result_dir/page-flip.txt" 2>&1 || \
	page_flip_status=$?
exec 3>&-
if test "$page_flip_status" -ne 124 ||
	! grep -q '^freq:' "$result_dir/page-flip.txt"; then
	cat "$result_dir/page-flip.txt" >&2
	exit 1
fi
if ! sudo drm_info > "$result_dir/drm-info.txt" 2>&1; then
	cat "$result_dir/drm-info.txt" >&2
	exit 1
fi

castkms_debugfs=/sys/kernel/debug/dri/castkms
test "$(sudo sed -n 's/ .*//p' "$castkms_debugfs/name")" = castkms

# Hold an active atomic modeset while dropping DRM master so independent
# writeback clients can submit jobs against the same CRTC.
mkfifo "$runtime_dir/mode-gate"
exec 8<> "$runtime_dir/mode-gate"
mode_gate_open=1
sudo timeout --signal=TERM --kill-after=2s 45s \
	stdbuf --output=L --error=L modetest -a -M castkms \
	-s "$virtual_connector@$crtc_id:1024x768" \
	-P "$plane_id@$crtc_id:1024x768@XR24" -d \
	<&8 > "$result_dir/mode-holder.txt" 2>&1 &
mode_holder_pid=$!
mode_active=0
for attempt in $(seq 1 50); do
	if ! kill -0 "$mode_holder_pid" 2>/dev/null; then
		cat "$result_dir/mode-holder.txt" >&2
		exit 1
	fi
	if sudo modetest -a -M castkms -p \
			> "$result_dir/mode-state.txt" 2>&1 &&
		awk -v crtc="$crtc_id" '
			$1 == crtc && $4 == "(1024x768)" { found = 1 }
			END { exit !found }
		' "$result_dir/mode-state.txt"; then
		mode_active=1
		break
	fi
	sleep 0.1
done
if test "$mode_active" -ne 1 ||
	grep -F 'Atomic Commit failed [1]' "$result_dir/mode-holder.txt" \
		>/dev/null; then
	cat "$result_dir/mode-holder.txt" >&2
	exit 1
fi

printf 'auto\n' | sudo tee "$castkms_debugfs/crtc-0/crc/control" >/dev/null
coproc CRC_CAPTURE {
	exec sudo timeout --signal=TERM --kill-after=1s 30s \
		cat "$castkms_debugfs/crtc-0/crc/data" \
		2> "$result_dir/crc-reader.txt"
}
crc_pid=$CRC_CAPTURE_PID
crc_fd=${CRC_CAPTURE[0]}
: > "$result_dir/crc.txt"
: > "$result_dir/crc-writeback.txt"
for sample in 1 2 3; do
	append_crc_record "$result_dir/crc.txt" baseline
done
printf '%s\n' 'composer_crc=pass' | tee -a "$result_dir/summary.txt"

run_writeback 1
run_writeback 2
cmp -s "$result_dir/writeback-1.raw" "$result_dir/writeback-2.raw"
sha256sum "$result_dir/writeback-1.raw" "$result_dir/writeback-2.raw" \
	> "$result_dir/writeback-sha256.txt"

# Discard records already queued during the second job. Reaching an
# inter-frame gap before requiring new records prevents buffered output from
# hiding a composer that stopped during writeback cleanup.
drained=0
while test "$drained" -lt 1024; do
	if ! IFS= read -r -t 0.001 -u "$crc_fd" line; then
		break
	fi
	if [[ ! $line =~ ^0x[[:xdigit:]]{8}\ 0x[[:xdigit:]]{8}$ ]]; then
		printf 'malformed queued CRC record: %s\n' "$line" >&2
		exit 1
	fi
	printf '%s\n' "$line" >> "$result_dir/crc-writeback.txt"
	drained=$((drained + 1))
done
test "$drained" -lt 1024
for sample in 1 2 3; do
	append_crc_record "$result_dir/crc-writeback.txt" \
		'post-writeback cleanup'
done
printf '%s\n' 'composer_writeback_overlap=pass' | \
	tee -a "$result_dir/summary.txt"

exec {crc_fd}<&-
crc_fd=
wait "$crc_pid" 2>/dev/null || true
crc_pid=

printf '\n' >&8
exec 8>&-
mode_gate_open=0
mode_holder_status=0
wait "$mode_holder_pid" || mode_holder_status=$?
mode_holder_pid=
if test "$mode_holder_status" -ne 0; then
	cat "$result_dir/mode-holder.txt" >&2
	exit 1
fi

sudo rmmod castkms
cast_loaded=0
if lsmod | grep -Eq '^(vkms|castkms)\b'; then
	printf '%s\n' 'module cleanup check failed' >&2
	exit 1
fi
test ! -e /sys/kernel/config/vkms
test ! -e /sys/kernel/config/castkms

printf '%s\n' 'cleanup=pass' | tee -a "$result_dir/summary.txt"
printf '%s\n' 'result=pass' | tee -a "$result_dir/summary.txt"
