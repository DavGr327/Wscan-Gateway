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
| `bitrate` | `can1.bitrate=500000` | velocità del bus; tutti i dispositivi del bus devono avere la stessa |
| `mode` | `can1.mode=loopback` | `normal` o `loopback` |
| `filter` | `can1.filter=allow:100-1FF` | solo questi ID (esadecimali); `block:` li scarta, `-ext` per gli ID a 29 bit, `off` lascia passare tutto |
| `packet_ms`, `packet_frames` | `can1.packet_ms=5` | ogni quanto / dopo quanti frame il gateway spedisce |
| `name` | `can1.name=can0` | nome dell'interfaccia (es. `can0` per CODESYS) |
| `on` | `can2.on=false` | usare il canale o no |
| `port` | `can1.port=21001` | porta TCP, cambiata sul gateway e nel ponte |

```bash
wscan set can1.bitrate=500000      # provo
candump vcan0                       # funziona?
wscan save                          # sì: la tengo
wscan revert                        # no: torno com'era
wscan set can2.on=false --save      # già sicuro: imposto e salvo insieme
```

`wscan -h` e `wscan set -h` spiegano tutto. Con `sudo` usa `sudo ./wscan ...` dalla cartella.

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
