#!/usr/bin/env python3
import unittest

from jieqi_state_adapter import (
    encode_jieqi_move,
    fen_piece_for_view,
    resolve_move_reveals,
)


class FairFenTests(unittest.TestCase):
    def test_hidden_truth_is_redacted(self):
        self.assertEqual(fen_piece_for_view("r7", "r4", False), "X")
        self.assertEqual(fen_piece_for_view("b7", "b4", False), "x")

    def test_open_role_is_visible(self):
        self.assertEqual(fen_piece_for_view("r7", "r4", True), "R")
        self.assertEqual(fen_piece_for_view("b7", "b6", True), "n")


class RevealTests(unittest.TestCase):
    def resolve(self, **overrides):
        args = dict(
            wire_reveal=None,
            mover_dark=False,
            flip_move=False,
            captured_dark=False,
            mover_is_bot=False,
            source_truth=None,
            target_truth=None,
        )
        args.update(overrides)
        return resolve_move_reveals(**args)

    def test_dark_mover_uses_wire_reveal(self):
        self.assertEqual(
            self.resolve(wire_reveal="R", mover_dark=True, source_truth="R"),
            ("R", None),
        )

    def test_dark_mover_repairs_missing_wire_reveal(self):
        self.assertEqual(
            self.resolve(mover_dark=True, source_truth="N"),
            ("N", None),
        )

    def test_open_bot_mover_learns_dark_capture(self):
        self.assertEqual(
            self.resolve(
                wire_reveal="p",
                captured_dark=True,
                mover_is_bot=True,
                target_truth="p",
            ),
            (None, "p"),
        )

    def test_dark_bot_mover_and_dark_capture_get_two_roles(self):
        self.assertEqual(
            self.resolve(
                wire_reveal="C",
                mover_dark=True,
                captured_dark=True,
                mover_is_bot=True,
                source_truth="C",
                target_truth="n",
            ),
            ("C", "n"),
        )

    def test_opponent_dark_capture_truth_is_not_exposed(self):
        self.assertEqual(
            self.resolve(
                wire_reveal="P",
                captured_dark=True,
                mover_is_bot=False,
                target_truth="P",
            ),
            (None, None),
        )

    def test_tokens_have_engine_semantics(self):
        self.assertEqual(encode_jieqi_move("a3a4"), "a3a4")
        self.assertEqual(encode_jieqi_move("a3a4", "R"), "a3a4R")
        # An open mover capturing a covered piece has captured role at byte 5.
        self.assertEqual(encode_jieqi_move("a3a4", captured_reveal="p"), "a3a4p")
        self.assertEqual(encode_jieqi_move("a3a4", "R", "p"), "a3a4Rp")


if __name__ == "__main__":
    unittest.main()
