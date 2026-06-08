systemctl stop eos
rm -rf ~/.local/share/net.akkudoktor.eos_broken
mv -f ~/.local/share/net.akkudoktor.eos ~/.local/share/net.akkudoktor.eos_broken
systemctl start eos
sleep 5
systemctl start mqtt_eos_bridge