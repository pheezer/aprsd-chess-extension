"""Feeds packets addressed to this station into the ClientGameManager."""

from __future__ import annotations

import logging

from aprsd.threads import rx

LOG = logging.getLogger("APRSD")


class ChessClientPacketThread(rx.APRSDProcessPacketThread):
    """aprsd ACKs messages for us before calling these hooks."""

    def __init__(self, packet_queue, manager, sent_messages):
        super().__init__(packet_queue=packet_queue)
        self.manager = manager
        # msgNo -> text of the messages we sent.  aprsd drops an acked
        # packet from PacketTrack before our ack hook runs, so keep our own.
        self.sent_messages = sent_messages

    def process_our_message_packet(self, packet):
        try:
            self.manager.on_message(packet.from_call, packet.message_text or "")
        except Exception:
            LOG.exception("Chess client: failed to handle a message")
        return False

    def process_ack_packet(self, packet):
        super().process_ack_packet(packet)
        self.manager.on_ack(self.sent_messages.pop(packet.msgNo, None))

    def process_piggyback_ack(self, packet):
        super().process_piggyback_ack(packet)
        self.manager.on_ack(self.sent_messages.pop(packet.ackMsgNo, None))
