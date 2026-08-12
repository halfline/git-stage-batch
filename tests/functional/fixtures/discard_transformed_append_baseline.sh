#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only

set -euo pipefail

repo_dir=${1:-$HOME/castkms}
expected_release=${2:?missing expected kernel release}
result_dir=$repo_dir/test-results/vm-smoke
stock_loaded=0
cast_loaded=0
configfs_dev=/sys/kernel/config/castkms/lifetime-test

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

running_release=$(uname -r)
test "$running_release" = "$expected_release"
printf 'kernel=%s\n' "$running_release" | tee "$result_dir/summary.txt"

make clean
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

if strings ./castkms.ko | grep -qi vkms; then
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

sudo rmmod castkms
cast_loaded=0
sudo rmmod vkms
stock_loaded=0

sudo insmod ./castkms.ko \
	create_default_dev=1 \
	enable_cursor=0 \
	enable_overlay=0 \
	enable_writeback=0 \
	enable_plane_pipeline=1
cast_loaded=1

sudo udevadm settle
ls -l /dev/dri | tee "$result_dir/dev-dri.txt"
if ! sudo modetest -M castkms -c -p -e > "$result_dir/modetest.txt" 2>&1; then
	cat "$result_dir/modetest.txt" >&2
	exit 1
fi

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

if ! mountpoint -q /sys/kernel/debug; then
	sudo mount -t debugfs none /sys/kernel/debug
fi
castkms_debugfs=/sys/kernel/debug/dri/castkms
test "$(sudo sed -n 's/ .*//p' "$castkms_debugfs/name")" = castkms
printf 'auto\n' | sudo tee "$castkms_debugfs/crtc-0/crc/control" >/dev/null
sudo timeout 5s dd if="$castkms_debugfs/crtc-0/crc/data" bs=23 count=3 \
	status=none | tr -d '\000' | tee "$result_dir/crc.txt"
test "$(wc -l < "$result_dir/crc.txt")" -eq 3
printf '%s\n' 'composer_crc=pass' | tee -a "$result_dir/summary.txt"

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
