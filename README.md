# SDN Broadcast Control

## Problem Statement

This project addresses a real-world networking problem: broadcast storms and unnecessary network congestion.
In large or poorly configured networks, excessive broadcast traffic (e.g., ARP requests) can degrade performance, increase latency, and even lead to outages.
By implementing broadcast detection and control in an SDN environment, the project demonstrates how modern programmable networks can improve efficiency, scalability, and resilience.
This is especially relevant in data centers, IoT environments, and edge systems where many devices may generate frequent broadcast traffic.

---

## Setup

```bash
sudo apt update && sudo apt upgrade -y
sudo apt update && sudo apt install python3-full -y
pip install ryu
sudo apt install mininet -y
sudo apt install openvswitch-switch python3-pip -y
```

> **Important:** Edit the Ryu WSGI file before proceeding.
>
> Open `/usr/local/lib/python3.8/dist-packages/ryu/app/wsgi.py` and search for `ALREADY_HANDLED`.
>
> Change:
> ```python
> from eventlet.wsgi import ALREADY_HANDLED
> ```
> To:
> ```python
> ALREADY_HANDLED = object()
> ```

---

## Execution Steps

### Terminal 1 — Ryu Controller

```bash
sudo -i
cd /home/vaishali/sdn
ryu-manager broadcast_control.py
```

> 📸 **Screenshot 1** — Ryu SDN Controller startup

---

### Terminal 2 — Mininet

```bash
sudo -i
sudo mn -c
sudo mn --topo tree,depth=2,fanout=2 --controller=remote --switch ovsk,protocols=OpenFlow13
```

> 📸 **Screenshot 2** — Mininet tree topology (depth=2, fanout=2) started

```
mininet> net
mininet> dump
```

> 📸 **Screenshot 3** — Topology with 4 hosts and 3 switches

---

### Terminal 3 — Flow Monitor

```bash
sudo -i
watch -n 2 'echo "=== S1 ===" && sudo ovs-ofctl -O OpenFlow13 dump-flows s1 && echo "=== S2 ===" && sudo ovs-ofctl -O OpenFlow13 dump-flows s2 && echo "=== S3 ===" && sudo ovs-ofctl -O OpenFlow13 dump-flows s3'
```

---

### Terminal 2 — Traffic Generation

**Step 1: Baseline normal traffic**

```
mininet> h1 ping -c 5 h2
```

> 📸 **Screenshot 4** — Normal unicast traffic and selective forwarding rules on s2

---

**Step 2: Check flows**

Open a new terminal (Terminal 4):

```bash
sudo -i
sudo ovs-ofctl -O OpenFlow13 dump-flows s1
sudo ovs-ofctl -O OpenFlow13 dump-flows s2
sudo ovs-ofctl -O OpenFlow13 dump-flows s3
```

> 📸 **Screenshot 5** — Flow tables showing priority 1 rules

---

**Step 3: Cross-switch traffic**

```
mininet> h1 ping -c 5 h3
mininet> h1 ping -c 5 h4
```

> 📸 **Screenshot 6** — Flow rules installed on core switch s1

---

**Step 4: iperf baseline**

```
mininet> iperf h1 h2
```

> 📸 **Screenshot 7** — iperf baseline throughput

---

**Step 5: Broadcast storm**

```
h1 ping -b -c 50 -i 0.05 10.255.255.255
```

Switch to Terminal 1 while running.

> 📸 **Screenshot 8** — Broadcast storm detected and dropped by controller

---

**Step 6: Legitimate traffic after storm**

```
mininet> h1 ping -c 5 h2
```

> 📸 **Screenshot 9** — Normal traffic unaffected after broadcast storm

---

**Step 7: iperf after storm**

```
mininet> iperf h1 h2
```

> 📸 **Screenshot 10** — iperf throughput maintained after storm

---

**Step 8: Full connectivity test**

```
mininet> pingall
```

> 📸 **Screenshot 11** — Full connectivity with no packet loss

---

**Step 9: Regression test**

In Terminal 4:

```bash
sudo ovs-ofctl -O OpenFlow13 del-flows s2
sudo ovs-ofctl -O OpenFlow13 dump-flows s2
```

> 📸 **Screenshot 12** — Flows deleted from s2

Back in Terminal 2:

```
mininet> h1 ping -c 3 h2
```

In Terminal 4:

```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows s2
```

> 📸 **Screenshot 13** — Flows reinstalled by controller

---

**Step 10: View metrics file**

In Terminal 4:

```bash
cat /home/vaishali/sdn/metrics.csv
```

> 📸 **Screenshot 14** — Metrics logged in CSV

Open the CSV file.

> 📸 **Screenshot 15** — Broadcast storm entries showing forwarded and dropped packets

---

## Proof of Execution

Screenshots are in the `screenshots/` folder.
