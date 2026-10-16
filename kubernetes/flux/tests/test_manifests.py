import pathlib
import re
import subprocess
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = ROOT.parent


def build(path):
    result = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return list(yaml.safe_load_all(result.stdout))


class FluxManifestsTest(unittest.TestCase):
    def test_cluster_wires_isolated_apps_without_pruning(self):
        resources = build("flux/clusters/homelab")
        root = next(item for item in resources if item["kind"] == "Kustomization" and item["metadata"]["name"] == "homelab")
        self.assertEqual((REPOSITORY_ROOT / root["spec"]["path"]).resolve(), ROOT / "flux/clusters/homelab")
        build(REPOSITORY_ROOT / root["spec"]["path"])
        apps = [item for item in resources if item["metadata"]["name"].startswith("app-")]
        self.assertEqual(len(apps), 4)
        for app in apps:
            with self.subTest(app=app["metadata"]["name"]):
                self.assertFalse(app["spec"]["suspend"])
                self.assertFalse(app["spec"]["prune"])
                self.assertEqual(app["spec"]["deletionPolicy"], "Orphan")
                self.assertEqual(
                    app["spec"]["postBuild"]["substituteFrom"],
                    [{"kind": "ConfigMap", "name": "ingress-hostnames", "optional": False}],
                )
                build(REPOSITORY_ROOT / app["spec"]["path"])
        self.assertFalse(any(item["kind"] == "ConfigMap" for item in resources))

    def test_hostnames_are_declared_and_no_secrets_are_rendered(self):
        example = yaml.safe_load((ROOT / "flux/ingress-hostnames.example.yaml").read_text())
        variables = set()
        for app in ("adguard", "my-portfolio", "stirling-pdf", "opencloud"):
            resources = build(f"flux/apps/{app}")
            self.assertFalse(any(item["kind"] == "Secret" for item in resources))
            variables.update(re.findall(r"\$\{([A-Z_]+)\}", yaml.safe_dump(resources)))
        self.assertEqual(variables, set(example["data"]))

    def test_adguard_pins_running_image_and_preserves_identity(self):
        release = build("flux/apps/adguard")[0]
        self.assertEqual(release["spec"]["releaseName"], "adguard")
        self.assertEqual(release["metadata"]["namespace"], "adguard")
        chart = release["spec"]["chart"]["spec"]
        self.assertEqual(chart["version"], "0.1.0")
        self.assertTrue((REPOSITORY_ROOT / chart["chart"] / "Chart.yaml").is_file())
        self.assertRegex(release["spec"]["values"]["image"]["tag"], r"@sha256:[a-f0-9]{64}$")

    def test_portfolio_pins_running_image(self):
        resources = build("flux/apps/my-portfolio")
        deployment = next(item for item in resources if item["kind"] == "Deployment")
        image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        self.assertRegex(image, r"@sha256:[a-f0-9]{64}$")

    def test_opencloud_uses_secret_and_pins_both_containers(self):
        resources = build("flux/apps/opencloud")
        deployment = next(item for item in resources if item["kind"] == "Deployment")
        spec = deployment["spec"]["template"]["spec"]
        containers = spec["initContainers"] + spec["containers"]
        self.assertEqual(len(containers), 2)
        self.assertEqual(len({container["image"] for container in containers}), 1)
        for container in containers:
            self.assertRegex(container["image"], r"@sha256:[a-f0-9]{64}$")
            password = next(env for env in container["env"] if env["name"] == "IDM_ADMIN_PASSWORD")
            self.assertNotIn("value", password)
            self.assertEqual(
                password["valueFrom"]["secretKeyRef"],
                {"name": "opencloud-admin", "key": "password"},
            )
        claims = {item["metadata"]["name"]: item["spec"] for item in resources if item["kind"] == "PersistentVolumeClaim"}
        self.assertEqual(set(claims), {"opencloud-data", "opencloud-config"})
        for name, size in (("opencloud-data", "20Gi"), ("opencloud-config", "5Gi")):
            self.assertEqual(claims[name]["storageClassName"], "longhorn")
            self.assertEqual(claims[name]["accessModes"], ["ReadWriteOnce"])
            self.assertEqual(claims[name]["resources"]["requests"]["storage"], size)
        self.assertNotIn("OPENCLOUD_ADMIN_PASSWORD", yaml.safe_dump(resources))
        self.assertNotIn("OPENCLOUD_IMAGE", yaml.safe_dump(resources))

    def test_stirling_values_and_chart_are_pinned(self):
        resources = build("flux/apps/stirling-pdf")
        release = next(item for item in resources if item["kind"] == "HelmRelease")
        config = next(item for item in resources if item["kind"] == "ConfigMap")
        self.assertEqual(release["spec"]["chart"]["spec"]["version"], "3.1.0")
        self.assertEqual(release["spec"]["valuesFrom"][0]["name"], config["metadata"]["name"])
        values = yaml.safe_load(config["data"]["values.yaml"])
        self.assertEqual(values["image"]["tag"], "3.1.0")
        refs = [env["valueFrom"]["secretKeyRef"] for env in values["envs"] if "valueFrom" in env]
        self.assertEqual({ref["name"] for ref in refs}, {"stirling-pdf-admin"})
        self.assertEqual({ref["key"] for ref in refs}, {"username", "password"})


if __name__ == "__main__":
    unittest.main()
