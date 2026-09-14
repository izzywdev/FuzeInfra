from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVISIONING = ROOT / "terraform" / "contabo" / "provisioning.tf"


def test_control_plane_bootstrap_avoids_raw_curl_pipe_to_shell():
    text = PROVISIONING.read_text()
    assert "curl -sfL https://get.k3s.io |" not in text
    assert "curl -sfL -o /tmp/install.sh https://get.k3s.io" in text
    assert "/tmp/install.sh || true" in text


def test_control_plane_bootstrap_verifies_k3s_installer_checksum():
    text = PROVISIONING.read_text()
    assert "install.sh.sha256sum" in text
    assert "sha256sum -c install.sh.sha256sum" in text
    assert "chmod 700 /tmp/install.sh" in text


def test_control_plane_bootstrap_uses_the_repo_k3s_channel_pin():
    text = PROVISIONING.read_text()
    assert "export INSTALL_K3S_CHANNEL='${var.k3s_channel}'" in text
    assert "export INSTALL_K3S_EXEC='--tls-san ${local.server_ip} --node-taint node-role.kubernetes.io/control-plane=:PreferNoSchedule'" in text
