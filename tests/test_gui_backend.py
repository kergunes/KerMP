from kermp.gui import StatusModel, detected_lan_ip, load_settings, save_settings


def test_status_model_is_observable():
    model = StatusModel(mode="host", display_name="Kerem")
    assert model.mode == "host"
    assert model.running is False
    assert model.last_error == ""


def test_lan_ip_returns_ipv4_like_value():
    value = detected_lan_ip()
    assert value.count(".") == 3
