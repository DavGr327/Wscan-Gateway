# wscan (italiano)

Usa il gateway **Waveshare 2-CH-CAN-TO-ETH** come se fosse una scheda CAN della tua macchina
Linux: `vcan0` e `vcan1` funzionano con `candump`, python-can, CODESYS, Wireshark e simili.
Versione completa in inglese: [README.md](README.md).

## Installazione

```bash
git clone https://github.com/DavGr327/Wscan-Gateway.git wscan
cd wscan
./install.sh
```

L'installazione fa qualche domanda, ognuna con un valore tra parentesi: Invio lo accetta.

- indirizzo del gateway (di fabbrica 192.168.0.7);
- come è collegato: stessa rete tramite switch, oppure cavo diretto su una porta Ethernet libera;
- se è cavo diretto: quale porta e che indirizzo dare a questo dispositivo;
- password della pagina web del gateway (di fabbrica `admin`);
- quali canali CAN usare, il nome delle interfacce, la velocità del bus.

Poi imposta tutto da sola: rete (con ripristino automatico se qualcosa va storto), gateway,
comando `wscan` e servizio che tiene acceso il ponte anche dopo i riavvii.

| | |
|---|---|
| `./install.sh --defaults` | senza domande: valori di fabbrica (le domande dopo con `wscan setup`) |
| `./install.sh --no-service` | tutto tranne il servizio in background |
| `git pull && ./install.sh` | aggiorna; rispondi `n` per tenere la tua configurazione |
| `./uninstall.sh` | toglie servizio e comando `wscan`; cartella, configurazione e rete restano |

## Uso di tutti i giorni

| Comando | |
|---|---|
| `wscan show` | tutto a colpo d'occhio: canali, velocità, filtri, traffico |
| `wscan set can1.bitrate=500000` | cambia un'impostazione, subito attiva |
| `wscan save` / `wscan revert` | la tieni nella configurazione / la annulli |
| `wscan status` | funziona? |
| `wscan setup` | rifà le domande dell'installazione |

Impostazioni di `wscan set`, su `can1` o `can2` (anche più di una alla volta):

| Impostazione | Esempio | |
|---|---|---|
| `bitrate` | `can1.bitrate=500000` | velocità del bus in bit/s, 10000-1000000; tutti i dispositivi del bus devono avere la stessa |
| `mode` | `can1.mode=loopback` | `normal` (sul bus) o `loopback` (modalità di prova del gateway, senza bus) |
| `filter` | `can1.filter=allow:100-1FF` | quali frame arrivano, ID esadecimali, vedi sotto |
| `packet_ms` | `can1.packet_ms=5` | il gateway spedisce ogni N ms (1-1000, di fabbrica 10): meno ritardo, più pacchetti |
| `packet_frames` | `can1.packet_frames=50` | ... oppure appena ha N frame (1-1000, di fabbrica 50) |
| `name` | `can1.name=can0` | nome dell'interfaccia, massimo 15 caratteri (es. `can0` per CODESYS) |
| `on` | `can2.on=false` | usare il canale (`true`) o no (`false`) |
| `port` | `can1.port=21001` | porta TCP del canale, cambiata sul gateway e nel ponte |

Filtri, ID in esadecimale, un intervallo (`100-1FF`) o un ID solo (`101`):

| | |
|---|---|
| `filter=off` | passa tutto (di fabbrica) |
| `filter=allow:100-1FF` | passano solo gli ID standard (11 bit) da 0x100 a 0x1FF |
| `filter=block:100` | passa tutto tranne l'ID standard 0x100 |
| `filter=allow-ext:18FF0000-18FFFFFF` | passano solo questi ID estesi (29 bit) |
| `filter=block-ext:18FF50E5` | passa tutto tranne questo ID esteso |

Visto al banco: `allow:` sugli ID standard blocca anche tutti i frame estesi.

```bash
wscan set can1.bitrate=500000      # provo
candump vcan0                       # funziona?
wscan save                          # sì: la tengo
wscan revert                        # no: torno com'era
wscan set can2.on=false --save      # già sicuro: imposto e salvo insieme
```

Ogni modifica al gateway lo fa ripartire: i canali si fermano qualche secondo, poi il ponte si
ricollega da solo. Con `--no-restart` la scrive senza riavvio (vale dal riavvio successivo).
`wscan -h` e `wscan set -h` spiegano tutto.

## Altri comandi

| Comando | Cosa fa |
|---|---|
| `wscan pending` | modifiche provate con `set` e non ancora salvate |
| `wscan discover` / `sudo wscan discover --arp eth0` | trova il gateway in rete (anche senza IP su quella porta) |
| `wscan net check` | come questo dispositivo raggiunge il gateway |
| `wscan gw show` / `wscan gw backup` | tutte le impostazioni del gateway / copia su file |
| `wscan dump` / `wscan send 123#DEADBEEF` | parla col gateway direttamente, senza ponte |
| `wscan config` | quale file di configurazione è in uso e cosa contiene |

## Dove montare il gateway

- **Stessa rete tramite switch** (caso reale): dai al gateway un IP fisso di quella rete dalla sua
  pagina web (*Network*), poi nel setup rispondi `1`.
- **Cavo diretto** (banco, o per tenere il bus fuori dalla rete d'impianto): rispondi `2`.

Mai due interfacce di questo dispositivo sulla stessa sottorete (Wi-Fi ed Ethernet entrambe su
192.168.1.x): il TCP va e viene. Il setup lo impedisce.

## Sicurezza

Le porte dati del gateway non chiedono login: chi è sulla stessa rete può mandare frame sul
bus. Meglio una rete o VLAN dedicata, e cambiare la password `admin/admin` del gateway. La
password non finisce mai nel file di configurazione: il setup la tiene in `~/.config/wscan/auth`,
leggibile solo da te.
