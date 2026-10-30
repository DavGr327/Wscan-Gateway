# wscan (italiano)

Ponte SocketCAN e comandi per il gateway **Waveshare 2-CH-CAN-TO-ETH**. Il gateway trasforma
ogni canale CAN in una porta TCP; wscan la ritrasforma in un'interfaccia CAN di Linux
(`vcan0`, `vcan1`), usabile da `candump`, python-can, CODESYS, Wireshark e simili.
Versione completa in inglese: [README.md](README.md).

## Partire

```bash
git clone https://github.com/DavGr327/Wscan-Gateway.git wscan && cd wscan
./install.sh                 # config.toml dall'esempio, comando wscan, servizio del ponte
wscan discover               # dov'è il gateway?
nano config.toml             # indirizzo del gateway, canali, eventuale [network]
sudo systemctl restart wscan-bridge
wscan status
```

Senza installare niente funziona anche `./wscan <comando>` direttamente dalla cartella.

## Dove montare il gateway

- **Stessa rete della Pi, tramite switch** (caso reale): al gateway un IP fisso di quella rete
  (pagina web *Network*), lo scrivi in `[gateway] host` e basta.
- **Cavo diretto su una porta libera** (banco, o per tenere il bus fuori dalla rete d'impianto):
  serve un IP della Pi sulla sottorete del gateway. Lo scrivi in `[network]` e lo imposta
  `sudo wscan net apply`, con ripristino automatico se qualcosa va storto.

Mai due interfacce della Pi sulla stessa sottorete (Wi-Fi ed Ethernet entrambe su
192.168.1.x): il TCP va e viene. `wscan net check` lo segnala.

## Cambiare la velocità del bus

Nel `config.toml`, sotto il canale:

```toml
[channel.gateway]
baud = 500000
```

poi `wscan gw apply`: scrive, verifica e riavvia il gateway; il ponte si ricollega da solo.
La stessa velocità va impostata su tutti i dispositivi del bus.

## Sicurezza

Le porte dati del gateway non chiedono login: chi è sulla stessa rete può mandare frame sul
bus della macchina. Meglio una rete o VLAN dedicata, e cambiare la password `admin/admin`.
La password del gateway non va mai nel `config.toml`: la legge da `$WSCAN_PASSWORD` o da
`~/.config/wscan/auth` (`chmod 600`), oppure la chiede.

## Comandi principali

`wscan status` · `wscan discover` · `wscan net check` · `wscan gw show` ·
`wscan gw set can1 baud=500000 --restart` · `wscan gw apply` · `wscan gw backup` ·
`wscan dump` · `wscan send 123#DEADBEEF`
