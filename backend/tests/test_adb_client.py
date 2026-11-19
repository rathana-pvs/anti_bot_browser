import pytest

from backend.services import adb_client


PRIVACY_OK = """location=false 
op_COARSE_LOCATION=COARSE_LOCATION: ignore; rejectTime=+3m12s ago 
op_FINE_LOCATION=FINE_LOCATION: ignore 
wifi_scan=0 
ble_scan=0 
mobile_data=0 
sim_state=ABSENT,ABSENT 
sim_country=, 
app_locales=Locales for com.facebook.katana for user 10 are [en_US] 
"""


def test_privacy_state_reads_a_locked_down_phone():
    assert adb_client.parse_privacy_state(PRIVACY_OK, "en-US") == {
        "location_off": True, "facebook_location_blocked": True, "scanning_off": True, "mobile_data_off": True,
        "sim_present": False, "sim_country": None, "language": "en-US"}


def test_privacy_state_notices_location_a_sim_and_another_language():
    out = (PRIVACY_OK.replace("location=false", "location=true")
           .replace("FINE_LOCATION: ignore", "FINE_LOCATION: allow")
           .replace("sim_state=ABSENT,ABSENT", "sim_state=LOADED,ABSENT").replace("sim_country=,", "sim_country=kh,")
           .replace("[en_US]", "[km_KH]").replace("mobile_data=0", "mobile_data=1"))
    state = adb_client.parse_privacy_state(out, "en-US")
    assert state["location_off"] is False and state["facebook_location_blocked"] is False
    assert state["sim_present"] is True and state["sim_country"] == "KH"
    assert state["language"] is None and state["mobile_data_off"] is False


def test_privacy_state_without_answers_is_unknown_not_safe():
    state = adb_client.parse_privacy_state("", "en-US")
    assert state["location_off"] is None and state["facebook_location_blocked"] is None


def test_apply_privacy_refuses_odd_input():
    client = adb_client.AdbClient.__new__(adb_client.AdbClient)
    with pytest.raises(ValueError):
        client.apply_privacy("PIXEL", 10, language="en-US; reboot")


def test_a_silent_failure_names_the_command_and_exit_code(monkeypatch):
    class Done:
        returncode, stdout, stderr = 137, "", ""

    monkeypatch.setattr(adb_client.subprocess, "run", lambda *a, **k: Done())
    client = adb_client.AdbClient(binary="adb")
    with pytest.raises(adb_client.AdbError) as error:
        client._execute(["-s", "SERIAL1", "shell", "uiautomator", "dump", "/sdcard/x.xml"])
    assert str(error.value) == "ADB failed with exit code 137: shell uiautomator dump /sdcard/x.xml"
    Done.stderr = "Killed"
    with pytest.raises(adb_client.AdbError, match=r"^Killed \(shell uiautomator dump"):
        client._execute(["-s", "SERIAL1", "shell", "uiautomator", "dump", "/sdcard/x.xml"])


def _fake_adb(tmp_path, body):
    """An `adb` that runs `body` as a shell script with the streamed file on its stdin."""
    script = tmp_path / "adb"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(0o755)
    client = adb_client.AdbClient(binary=str(script))
    client._transports = {}
    return client


def test_write_content_streams_the_whole_file(tmp_path):
    received = tmp_path / "received"
    client = _fake_adb(tmp_path, f"cat > {received}")
    local = tmp_path / "photo.jpg"
    local.write_bytes(b"jpeg bytes")
    assert client.write_content("PIXEL", 11, "content://x/media?name=a.jpg", str(local)) is True
    assert received.read_bytes() == b"jpeg bytes"


def test_write_content_stops_once_the_receiver_gave_up(tmp_path):
    """The companion app stops reading when the gallery refuses a file; the write must not hang."""
    import time
    client = _fake_adb(tmp_path, "exec sleep 30")
    local = tmp_path / "photo.jpg"
    local.write_bytes(b"jpeg bytes")
    asked = []
    started = time.monotonic()
    assert client.write_content("PIXEL", 11, "content://x/media?name=a.jpg", str(local), timeout=20,
                                stop_when=lambda: asked.append(1) or len(asked) >= 2, check_every_s=0.05) is False
    assert time.monotonic() - started < 5 and len(asked) == 2


def test_write_content_times_out_and_reports_errors(tmp_path):
    local = tmp_path / "photo.jpg"
    local.write_bytes(b"jpeg bytes")
    with pytest.raises(adb_client.AdbError, match="timed out"):
        _fake_adb(tmp_path, "exec sleep 30").write_content("PIXEL", 11, "content://x", str(local), timeout=0.3,
                                                           check_every_s=0.05)
    with pytest.raises(adb_client.AdbError, match="Error while accessing provider"):
        _fake_adb(tmp_path, "echo 'Error while accessing provider' >&2; exit 1").write_content(
            "PIXEL", 11, "content://x", str(local))


DUPLICATE_LISTING = """List of devices attached
28251FDH300HW2         offline usb:3-2.2.7.2 transport_id:12
28251FDH300HW2         device usb:3-2.2.7.2 product:cheetah model:Pixel_7_Pro device:cheetah transport_id:329
"""


def _recording_client(monkeypatch, answer):
    """A client whose adb answers through `answer(args)` and remembers every call."""
    calls = []
    monkeypatch.setattr(adb_client, "canonical_serial", lambda serial: serial)
    client = adb_client.AdbClient(binary="adb")

    def execute(args, timeout=None):
        calls.append(args)
        return answer(args)
    client._execute = execute
    return client, calls


def test_a_serial_adb_lists_twice_is_reached_by_its_live_connection_id(monkeypatch):
    def answer(args):
        if args == ["devices", "-l"]:
            return DUPLICATE_LISTING
        if args[:2] == ["-s", "28251FDH300HW2"]:
            raise adb_client.AdbError("adb: more than one device with serial 28251FDH300HW2")
        return "28251FDH300HW2" if args[-1] == "ro.serialno" else "users"
    client, calls = _recording_client(monkeypatch, answer)
    [phone] = client.devices()
    assert phone["serial"] == "28251FDH300HW2" and phone["state"] == "device" and "target" not in phone and "transport_id" not in phone
    assert client.shell("28251FDH300HW2", "pm", "list", "users") == "users"
    assert calls[-1] == ["-t", "329", "shell", "pm", "list", "users"]


def test_a_duplicate_that_appears_after_listing_is_retried_once_on_the_live_connection(monkeypatch):
    listings = iter(["List of devices attached\n28251FDH300HW2 device usb:3-2 transport_id:7\n", DUPLICATE_LISTING])

    def answer(args):
        if args == ["devices", "-l"]:
            return next(listings)
        if args[-1] == "ro.serialno":
            return "28251FDH300HW2"
        if args[:2] == ["-s", "28251FDH300HW2"]:
            raise adb_client.AdbError("adb: more than one device with serial 28251FDH300HW2 (shell pm list users)")
        return "users"
    client, calls = _recording_client(monkeypatch, answer)
    client.devices()
    assert client.shell("28251FDH300HW2", "pm", "list", "users") == "users"
    assert calls[-1] == ["-t", "329", "shell", "pm", "list", "users"]


def test_other_adb_failures_are_not_retried(monkeypatch):
    def answer(args):
        if args == ["devices", "-l"]:
            return "List of devices attached\nPIXEL device usb:1 transport_id:3\n"
        if args[-1] == "ro.serialno":
            return "PIXEL"
        raise adb_client.AdbError("Killed (shell pm list users)")
    client, calls = _recording_client(monkeypatch, answer)
    client.devices()
    with pytest.raises(adb_client.AdbError, match="Killed"):
        client.shell("PIXEL", "pm", "list", "users")
    assert sum(args[-3:] == ["pm", "list", "users"] for args in calls) == 1


def test_grant_media_grants_the_reel_pickers_permission_too(monkeypatch):
    sent = []
    client = adb_client.AdbClient(binary="adb")
    monkeypatch.setattr(client, "shell", lambda serial, *command, timeout=None: sent.append(command) or "")
    client.grant_media("PIXEL", 16)
    [(script,)] = sent
    for permission in ("READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO", "READ_MEDIA_VISUAL_USER_SELECTED"):
        assert f"pm grant --user 16 com.facebook.katana android.permission.{permission} " in script
    assert script.endswith("; true")  # a permission older Android lacks does not fail the rest
