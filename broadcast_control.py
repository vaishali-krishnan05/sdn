from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet
from ryu.lib.packet import ether_types

import time
import csv
from datetime import datetime


class BroadcastControlWithMetrics(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    BROADCAST_THRESHOLD = 10
    TIME_WINDOW = 5  # seconds

    def __init__(self, *args, **kwargs):
        super(BroadcastControlWithMetrics, self).__init__(*args, **kwargs)

        self.mac_to_port = {}
        self.broadcast_tracker = {}
        self.total_packets = 0

        self.csv_file = open("metrics.csv", "w", newline="")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow([
            "timestamp", "src_mac", "dst_mac", "packet_type",
            "action", "broadcast_count", "total_packets"
        ])
        self.logger.info("📊 Metrics logging started")

    def log_metrics(self, src, dst, pkt_type, action, count):
        self.total_packets += 1
        self.csv_writer.writerow([
            datetime.now().strftime("%H:%M:%S"),
            src, dst, pkt_type, action, count, self.total_packets
        ])
        self.csv_file.flush()

    def is_broadcast_storm(self, src):
        now = time.time()
        if src not in self.broadcast_tracker:
            self.broadcast_tracker[src] = []
        self.broadcast_tracker[src] = [
            t for t in self.broadcast_tracker[src]
            if now - t < self.TIME_WINDOW
        ]
        self.broadcast_tracker[src].append(now)
        return len(self.broadcast_tracker[src]) > self.BROADCAST_THRESHOLD

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info(f"✅ Table-miss rule installed on dpid={datapath.id}")

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions)]

        if buffer_id and buffer_id != ofproto.OFP_NO_BUFFER:
            mod = parser.OFPFlowMod(
                datapath=datapath,
                buffer_id=buffer_id,
                priority=priority,
                match=match,
                instructions=inst
            )
        else:
            mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=priority,
                match=match,
                instructions=inst
            )

        datapath.send_msg(mod)
        self.logger.info(
            f"✅ Flow installed on dpid={datapath.id} | "
            f"priority={priority} | match={match}"
        )

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst = eth.dst
        src = eth.src

        # Learn source MAC → port mapping for this switch
        self.mac_to_port[dpid][src] = in_port

        self.logger.info(
            f"[dpid={dpid}] PacketIn | src={src} dst={dst} "
            f"in_port={in_port} | mac_table={self.mac_to_port[dpid]}"
        )

        # ── Broadcast handling ────────────────────────────────────────────
        if dst == 'ff:ff:ff:ff:ff:ff':
            is_storm = self.is_broadcast_storm(src)
            count = len(self.broadcast_tracker.get(src, []))

            if is_storm:
                self.logger.info(
                    f"[dpid={dpid}] ❌ Dropping broadcast storm from {src} "
                    f"(count={count})"
                )
                self.log_metrics(src, dst, "broadcast", "dropped", count)
                return

            self.logger.info(
                f"[dpid={dpid}] ⚠️ Controlled broadcast from {src} "
                f"(count={count})"
            )
            self.log_metrics(src, dst, "broadcast", "forwarded", count)
            actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]

        # ── Unicast handling ──────────────────────────────────────────────
        else:
            if dst in self.mac_to_port[dpid]:
                out_port = self.mac_to_port[dpid][dst]
                self.logger.info(
                    f"[dpid={dpid}] ✅ Known dst={dst} → port {out_port}"
                )
            else:
                out_port = ofproto.OFPP_FLOOD
                self.logger.info(
                    f"[dpid={dpid}] ❓ Unknown dst={dst} → flooding"
                )

            actions = [parser.OFPActionOutput(out_port)]
            self.log_metrics(src, dst, "unicast", "forwarded", 0)

            # Install flow only when destination port is known
            if out_port != ofproto.OFPP_FLOOD:
                match = parser.OFPMatch(
                    in_port=in_port,
                    eth_src=src,
                    eth_dst=dst
                )
                # If packet is buffered in the switch, pass buffer_id to
                # FlowMod so the switch forwards it through the new rule.
                # Return immediately — no separate PacketOut needed.
                if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                    self.add_flow(datapath, 1, match, actions, msg.buffer_id)
                    return

                self.add_flow(datapath, 1, match, actions)

        # ── Send packet out ───────────────────────────────────────────────
        # Reached when:
        #   • destination unknown → flood
        #   • controlled broadcast → flood
        #   • known dst but packet was NOT buffered (OFP_NO_BUFFER)
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )
        datapath.send_msg(out)
