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
