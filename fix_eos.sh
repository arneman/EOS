STOP_TIMEOUT_SECONDS="${STOP_TIMEOUT_SECONDS:-30}"
EOS_PROCESS_PATTERN="${EOS_PROCESS_PATTERN:-net\.akkudoktor\.eos}"

if ! timeout "${STOP_TIMEOUT_SECONDS}"s systemctl stop eos; then
	echo "systemctl stop eos timed out or failed after ${STOP_TIMEOUT_SECONDS}s, forcing EOS shutdown"
	systemctl kill --signal=SIGKILL eos || true
fi

if pgrep -f "${EOS_PROCESS_PATTERN}" >/dev/null; then
	echo "Found lingering EOS-related processes, forcing EOS shutdown"
	pgrep -fa "${EOS_PROCESS_PATTERN}" || true
	pkill -9 -f "${EOS_PROCESS_PATTERN}" || true
	sleep 1
fi

if systemctl is-active --quiet eos || pgrep -f "${EOS_PROCESS_PATTERN}" >/dev/null; then
	echo "EOS could not be stopped"
	pgrep -fa "${EOS_PROCESS_PATTERN}" || true
	exit 1
fi

rm -rf /root/.local/share/net.akkudoktor.eos_broken
mv -f /root/.local/share/net.akkudoktor.eos /root/.local/share/net.akkudoktor.eos_broken
systemctl start eos
sleep 5
systemctl start mqtt_eos_bridge
echo "Fixed EOS"
