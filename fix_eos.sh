STOP_TIMEOUT_SECONDS="${STOP_TIMEOUT_SECONDS:-40}"

if ! timeout "${STOP_TIMEOUT_SECONDS}"s systemctl stop eos; then
	echo "systemctl stop eos timed out or failed after ${STOP_TIMEOUT_SECONDS}s, forcing EOS shutdown"
	systemctl kill --signal=SIGKILL eos || true
fi

if systemctl is-active --quiet eos; then
	echo "EOS is still running after stop, forcing EOS shutdown"
	systemctl kill --signal=SIGKILL eos || true
	sleep 1
fi

if systemctl is-active --quiet eos; then
	echo "EOS could not be stopped"
	exit 1
fi

rm -rf /root/.local/share/net.akkudoktor.eos_broken
mv -f /root/.local/share/net.akkudoktor.eos /root/.local/share/net.akkudoktor.eos_broken
systemctl reset-failed eos || true
systemctl start eos
sleep 5
systemctl start mqtt_eos_bridge
echo "Fixed EOS"
