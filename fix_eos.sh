systemctl stop eos
rm -rf root/.local/share/net.akkudoktor.eos_broken
mv -f root/.local/share/net.akkudoktor.eos root/.local/share/net.akkudoktor.eos_broken
systemctl start eos
sleep 5
systemctl start mqtt_eos_bridge