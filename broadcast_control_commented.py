# Import base Ryu app class
from ryu.base import app_manager

# Import OpenFlow event types
from ryu.controller import ofp_event

# Import dispatcher states
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER

# Decorator to register event handlers
from ryu.controller.handler import set_ev_cls

# OpenFlow 1.3 protocol
from ryu.ofproto import ofproto_v1_3

# Packet parsing libraries
from ryu.lib.packet import packet, ethernet
from ryu.lib.packet import ether_types

# Utility libraries
import time
import csv
from datetime import datetime


# Main Ryu application class
class BroadcastControlWithMetrics(app_manager.RyuApp):

    # Use OpenFlow 1.3
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # Broadcast storm detection parameters
    #if a device sends more than 10 broadcasts in less than 5 secs->it is a storm
    BROADCAST_THRESHOLD = 10   # max allowed broadcasts
    TIME_WINDOW = 5            # time window in seconds

    def __init__(self, *args, **kwargs):
        super(BroadcastControlWithMetrics, self).__init__(*args, **kwargs)

        # MAC learning table: {dpid: {mac: port}}
        self.mac_to_port = {}

        # Broadcast tracking: {src_mac: [timestamps]}
        self.broadcast_tracker = {}

        # Total packets processed
        self.total_packets = 0

        # Open CSV file to log metrics
        self.csv_file = open("metrics.csv", "w", newline="")
        self.csv_writer = csv.writer(self.csv_file)

        # Write header row
        self.csv_writer.writerow([
            "timestamp", "src_mac", "dst_mac", "packet_type",
            "action", "broadcast_count", "total_packets"
        ])

        self.logger.info("📊 Metrics logging started")
    

    # Function to log packet details into CSV
    def log_metrics(self, src, dst, pkt_type, action, count):
        self.total_packets += 1

        self.csv_writer.writerow([
            datetime.now().strftime("%H:%M:%S"),  # current time
            src,                                  # source MAC
            dst,                                  # destination MAC
            pkt_type,                             # broadcast/unicast
            action,                               # forwarded/dropped
            count,                                # broadcast count
            self.total_packets                    # total packets seen
        ])

        # Ensure data is written immediately
        self.csv_file.flush()


    # Detect if a source is causing a broadcast storm
    def is_broadcast_storm(self, src):
        now = time.time()

        # Initialize list if first time
        if src not in self.broadcast_tracker:
            self.broadcast_tracker[src] = []

        # Keep only timestamps within TIME_WINDOW
        self.broadcast_tracker[src] = [
            t for t in self.broadcast_tracker[src]
            if now - t < self.TIME_WINDOW
        ]

        # Add current timestamp
        self.broadcast_tracker[src].append(now)

        # Check threshold
        return len(self.broadcast_tracker[src]) > self.BROADCAST_THRESHOLD


    # Handle switch connection event
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):

        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        # Match all packets (table-miss)
        match = parser.OFPMatch()

        # Send unmatched packets to controller
        actions = [parser.OFPActionOutput(
            ofproto.OFPP_CONTROLLER,
            ofproto.OFPCML_NO_BUFFER
        )]

        # Install table-miss flow
        self.add_flow(datapath, 0, match, actions)

        self.logger.info(f"✅ Table-miss rule installed on dpid={datapath.id}")


    # Function to install flow rules in switch
    def add_flow(self, datapath, priority, match, actions, buffer_id=None):

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        # Define actions to apply
        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions)]

        # If packet is buffered, include buffer_id
        if buffer_id and buffer_id != ofproto.OFP_NO_BUFFER:
            mod = parser.OFPFlowMod(
                datapath=datapath,
                buffer_id=buffer_id,
                priority=priority,
                match=match,
                instructions=inst
            )
        else:
            # Normal flow rule
            mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=priority,
                match=match,
                instructions=inst
            )

        # Send flow rule to switch
        datapath.send_msg(mod)

        self.logger.info(
            f"✅ Flow installed on dpid={datapath.id} | "
            f"priority={priority} | match={match}"
        )


    # Handle incoming packets from switch
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):

        msg = ev.msg
        datapath = msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        dpid = datapath.id

        # Initialize MAC table for switch
        self.mac_to_port.setdefault(dpid, {})

        # Port where packet arrived
        in_port = msg.match['in_port']

        # Parse packet
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # Ignore LLDP packets (used by topology discovery)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst = eth.dst
        src = eth.src

        # Learn source MAC → port
        self.mac_to_port[dpid][src] = in_port

        self.logger.info(
            f"[dpid={dpid}] PacketIn | src={src} dst={dst} "
            f"in_port={in_port} | mac_table={self.mac_to_port[dpid]}"
        )


        # ───── BROADCAST HANDLING ─────
        if dst == 'ff:ff:ff:ff:ff:ff':

            # Check if storm
            is_storm = self.is_broadcast_storm(src)

            count = len(self.broadcast_tracker.get(src, []))

            if is_storm:
                # Drop packet
                self.logger.info(
                    f"[dpid={dpid}] ❌ Dropping broadcast storm from {src} "
                    f"(count={count})"
                )

                self.log_metrics(src, dst, "broadcast", "dropped", count)
                return

            # Allow controlled broadcast
            self.logger.info(
                f"[dpid={dpid}] ⚠️ Controlled broadcast from {src} "
                f"(count={count})"
            )

            self.log_metrics(src, dst, "broadcast", "forwarded", count)

            # Flood packet
            actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]


        # ───── UNICAST HANDLING ─────
        else:

            # If destination known → send directly
            if dst in self.mac_to_port[dpid]:
                out_port = self.mac_to_port[dpid][dst]

                self.logger.info(
                    f"[dpid={dpid}] ✅ Known dst={dst} → port {out_port}"
                )
            else:
                # Unknown → flood
                out_port = ofproto.OFPP_FLOOD

                self.logger.info(
                    f"[dpid={dpid}] ❓ Unknown dst={dst} → flooding"
                )

            actions = [parser.OFPActionOutput(out_port)]

            self.log_metrics(src, dst, "unicast", "forwarded", 0)

            # Install flow only if destination is known
            if out_port != ofproto.OFPP_FLOOD:

                match = parser.OFPMatch(
                    in_port=in_port,
                    eth_src=src,
                    eth_dst=dst
                )

                # If packet buffered → install flow and return
                if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                    self.add_flow(datapath, 1, match, actions, msg.buffer_id)
                    return

                self.add_flow(datapath, 1, match, actions)


        # ───── SEND PACKET OUT ─────
        # Used for:
        #  - flooding
        #  - broadcast
        #  - unbuffered packets

        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )

        datapath.send_msg(out)