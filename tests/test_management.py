import json, sys, tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "management-agent"))
import management

class FakeResult:
    returncode=0; stdout="ok"; stderr=""
class ManagementTests(unittest.TestCase):
    def test_hash_and_duration_validation(self):
        self.assertEqual(management.validate_hash("a"*32), "a"*32)
        with self.assertRaises(ValueError): management.validate_hash("bad")
        self.assertEqual(management.validate_duration("2"), 2.0)
        with self.assertRaises(ValueError): management.validate_duration("99999")
    def test_allowlist_command_building(self):
        self.assertEqual(management.command_for("drop_announces", {})[-1], "--drop-announces")
        self.assertIn("--blackhole", management.command_for("blackhole", {"identity":"a"*32, "reason":"test"}))
        with self.assertRaises(ValueError): management.command_for("shell", {})
        with self.assertRaises(ValueError): management.command_for("reload", {"interface":"bad\nname"})
    def test_live_commands_are_requested_from_rnsd_helper_not_run_locally(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); old=(management.REQUESTS, management.RESULTS, management.time.sleep)
            management.REQUESTS=root/"requests"; management.RESULTS=root/"results"
            def helper_tick(_):
                request_path=next(management.REQUESTS.glob("*.json")); request=json.loads(request_path.read_text())
                self.assertEqual(request["action"], "drop_path")
                (management.RESULTS).mkdir(parents=True, exist_ok=True)
                (management.RESULTS/f"{request['operation_id']}.json").write_text(json.dumps({"operation_id":request["operation_id"],"operation":"drop_path","ok":True,"stdout":"done","stderr":""}))
            management.time.sleep=helper_tick
            try: result=management.run("drop_path", {"destination":"b"*32})
            finally: management.REQUESTS,management.RESULTS,management.time.sleep=old
        self.assertTrue(result["ok"]); self.assertEqual(result["operation"], "drop_path")
    def test_capability_detection(self):
        caps=management.capabilities("--blackhole --unblackhole --drop-via --drop-announces --attach --detach --reload")
        self.assertTrue(caps["blackhole"]); self.assertTrue(caps["reload_interface"])
    def test_partial_recovery_stops_deterministically(self):
        calls=[]
        def execute(op, payload):
            calls.append(op); return {"ok":op != "drop_via"}
        status, results=management.run_sequence([("blackhole",{}),("drop_via",{}),("reload",{})], execute)
        self.assertEqual(status, "Partially completed")
        self.assertEqual(calls, ["blackhole","drop_via"])

if __name__ == "__main__": unittest.main()
