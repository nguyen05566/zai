#!/usr/bin/env python3
import importlib
import threading
import unittest


BOT_MODULES = (
    "arena5", "arena6", "arena14", "arena15", "arena18", "arena19",
    "cup_bot_mistboard",
)


def standard_truth_pieces():
    # Server coordinates: row 0 red home rank, row 9 black home rank.
    pieces = []
    back_types = (4, 6, 3, 2, 1, 2, 3, 6, 4)
    for color, row in (("r", 0), ("b", 9)):
        for col, role in enumerate(back_types):
            pieces.append((f"{color}{role}", f"{color}{role}", row * 9 + col,
                           role == 1))
    for color, row in (("r", 2), ("b", 7)):
        for col in (1, 7):
            pieces.append((f"{color}5", f"{color}5", row * 9 + col, False))
    for color, row in (("r", 3), ("b", 6)):
        for col in (0, 2, 4, 6, 8):
            pieces.append((f"{color}7", f"{color}7", row * 9 + col, False))
    return pieces


class BotStateIntegrationTests(unittest.TestCase):
    def test_every_migrated_bot_redacts_initial_truth(self):
        pieces = standard_truth_pieces()
        self.assertEqual(len(pieces), 32)
        for module_name in BOT_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                bot = module.JieqiCupBot.__new__(module.JieqiCupBot)
                bot.board = module.XiangqiBoardTracker()
                fen = bot._rebuild_fen_with_current_flip(pieces)
                board = fen.split()[0]
                self.assertEqual(board.count("X"), 15)
                self.assertEqual(board.count("x"), 15)
                self.assertEqual(board.count("K"), 1)
                self.assertEqual(board.count("k"), 1)
                self.assertFalse(any(ch in board for ch in "ABNRCPabnrcp"))

    def test_bag_tracks_both_roles_in_six_character_token(self):
        module = importlib.import_module("arena14")
        board = module.XiangqiBoardTracker()
        token = board.record_move("a3a4", "R", "p")
        self.assertEqual(token, "a3a4Rp")
        self.assertIn("R1", board.bag_string())
        self.assertIn("p4", board.bag_string())

    def test_in_place_flip_keeps_truth_piece(self):
        module = importlib.import_module("arena14")
        board = module.VisibleBoard()
        board.cells[10] = "R"
        board.apply_move(10, 10)
        self.assertEqual(board.cells[10], "R")

    @staticmethod
    def make_handler_bot(module, source, target, source_truth, target_truth,
                         dark_positions, *, is_red, side_to_move, wire_reveal):
        class FakeMessage:
            def __init__(self, values):
                self.values = iter(values)

            def read_byte(self):
                return next(self.values)

        bot = module.JieqiCupBot.__new__(module.JieqiCupBot)
        bot._move_lock = threading.Lock()
        bot._move_recv_count = 0
        bot._move_skip_count = 0
        bot._move_error_count = 0
        bot._last_move_uci = None
        bot._last_move_time = 0.0
        bot._played_this_turn = False
        bot.last_action_timestamp = 0.0
        bot.board = module.XiangqiBoardTracker()
        bot.board.is_playing = True
        bot.board.is_red = is_red
        bot.board.side_to_move = side_to_move
        bot.board.dark_positions = set(dark_positions)
        bot.visible_board = module.VisibleBoard()
        bot.visible_board.cells[source] = source_truth
        bot.visible_board.cells[target] = target_truth
        bot._decoded_ws_event = {
            "source": source,
            "target": target,
            "revealed_piece": wire_reveal,
            "payload_hex": "test",
        }
        return bot, FakeMessage([source, target])

    def test_live_handler_encodes_two_roles_for_bot_dark_capture(self):
        module = importlib.import_module("arena14")
        bot, msg = self.make_handler_bot(
            module, 27, 36, "R", "p", {27, 36},
            is_red=True, side_to_move="w", wire_reveal="R",
        )
        bot._handle_move(msg)
        self.assertEqual(bot._move_error_count, 0)
        self.assertTrue(bot.board.uci_moves[0].endswith("Rp"))
        self.assertIn("R1", bot.board.bag_string())
        self.assertIn("p4", bot.board.bag_string())
        # A duplicate frame arrives after truth/state moved away from source;
        # coordinate-first dedup must stop it before reveal reconstruction.
        class DuplicateMessage:
            def __init__(self):
                self.values = iter((27, 36))
            def read_byte(self):
                return next(self.values)
        bot._handle_move(DuplicateMessage())
        self.assertEqual(len(bot.board.uci_moves), 1)
        self.assertEqual(bot._move_skip_count, 1)

    def test_live_handler_does_not_expose_opponent_dark_capture(self):
        module = importlib.import_module("arena14")
        bot, msg = self.make_handler_bot(
            module, 54, 45, "r", "P", {45},
            is_red=True, side_to_move="b", wire_reveal="P",
        )
        bot._handle_move(msg)
        self.assertEqual(bot._move_error_count, 0)
        self.assertEqual(len(bot.board.uci_moves[0]), 4)
        self.assertIn("P5", bot.board.bag_string())


if __name__ == "__main__":
    unittest.main()
