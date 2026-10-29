import os
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
        self.assertEqual(len(apps), 6)
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
        for app in ("adguard", "claimctl", "my-portfolio", "stirling-pdf", "opencloud", "pocket-id"):
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

    def claimctl_config(self):
        resources = build("flux/apps/claimctl")
        release = next(item for item in resources if item["kind"] == "HelmRelease")
        config = next(item for item in resources if item["kind"] == "ConfigMap")
        values = yaml.safe_load(config["data"]["values.yaml"])
        return release, config, values

    def test_claimctl_uses_external_credentials_and_hostname(self):
        release, config, values = self.claimctl_config()
        self.assertEqual(release["metadata"]["namespace"], "claimctl")
        self.assertEqual(release["spec"]["releaseName"], "claimctl")
        self.assertEqual(release["spec"]["chart"]["spec"]["version"], "0.1.0")
        repository = next(item for item in build("flux/apps/claimctl") if item["kind"] == "HelmRepository")
        self.assertEqual(repository["spec"]["type"], "oci")
        self.assertEqual(repository["spec"]["url"], "oci://ghcr.io/thetaqitahmid/claimctl/helm")
        self.assertEqual(release["spec"]["valuesFrom"][0]["name"], config["metadata"]["name"])
        self.assertEqual(config["metadata"]["labels"]["reconcile.fluxcd.io/watch"], "Enabled")
        self.assertFalse(values["keyGeneration"]["enabled"])
        for section in (values["db"], values["appEncryption"], values["postgres"]["settings"], values["postgres"]["userDatabase"]):
            self.assertEqual(section["existingSecret"], "claimctl-credentials")
        self.assertEqual(values["db"]["password"], "")
        self.assertEqual(values["postgres"]["settings"]["superuserPassword"]["value"], "")
        for key in ("name", "user", "password"):
            self.assertEqual(values["postgres"]["userDatabase"][key]["value"], "")
            self.assertEqual(values["postgres"]["userDatabase"][key]["secretKey"], f"db-{key}")
        self.assertEqual(values["ingress"]["hosts"][0]["host"], "${CLAIMCTL_HOST}")
        self.assertEqual(values["ingress"]["tls"][0]["hosts"], ["${CLAIMCTL_HOST}"])
        for component in ("backend", "frontend", "postgres"):
            self.assertRegex(release["spec"]["values"][component]["image"]["tag"], r"@sha256:[a-f0-9]{64}$")

    @unittest.skipUnless(os.environ.get("CLAIMCTL_CHART"), "Set CLAIMCTL_CHART to the unpacked 0.1.0 chart for Helm rendering")
    def test_claimctl_chart_renders_only_secret_references(self):
        release, _, _ = self.claimctl_config()
        result = subprocess.run(
            ["helm", "template", "claimctl", os.environ["CLAIMCTL_CHART"],
             "--namespace", "claimctl", "--values", str(ROOT / "claimctl/values.yaml"), "--values", "-"],
            input=yaml.safe_dump(release["spec"]["values"]), check=True, capture_output=True, text=True,
        )
        resources = list(yaml.safe_load_all(result.stdout))
        self.assertFalse(any(item["kind"] == "Secret" for item in resources))
        refs = []
        for item in resources:
            if item["kind"] not in ("Deployment", "StatefulSet"):
                continue
            spec = item["spec"]["template"]["spec"]
            self.assertNotIn("generate-app-key", [container["name"] for container in spec.get("initContainers", [])])
            for container in spec.get("initContainers", []) + spec["containers"]:
                for env in container.get("env", []):
                    if env["name"] in ("DB_USER", "DB_PASSWORD", "DB_NAME", "APP_ENCRYPTION_KEY", "POSTGRES_PASSWORD", "POSTGRES_DB", "USERDB_USER", "USERDB_PASSWORD"):
                        self.assertNotIn("value", env)
                        refs.append(env["valueFrom"]["secretKeyRef"])
        self.assertEqual({ref["name"] for ref in refs}, {"claimctl-credentials"})
        self.assertEqual({ref["key"] for ref in refs}, {"db-name", "db-user", "db-password", "admin-password", "app-encryption-key"})
        ingress = next(item for item in resources if item["kind"] == "Ingress")
        self.assertEqual(ingress["spec"]["rules"][0]["host"], "${CLAIMCTL_HOST}")
        self.assertEqual(ingress["spec"]["tls"][0]["hosts"], ["${CLAIMCTL_HOST}"])
        statefulset = next(item for item in resources if item["kind"] == "StatefulSet")
        self.assertEqual(statefulset["metadata"]["name"], "claimctl-postgres")
        claim = statefulset["spec"]["volumeClaimTemplates"][0]
        self.assertEqual(claim["metadata"]["name"], "postgres-data")
        self.assertEqual(claim["spec"]["storageClassName"], "longhorn")
        self.assertEqual(claim["spec"]["resources"]["requests"]["storage"], "1Gi")

    def test_pocket_id_uses_sqlite_and_persistent_external_encryption_key(self):
        resources = build("flux/apps/pocket-id")
        self.assertEqual(resources, build("pocket-id"))
        self.assertEqual({item["metadata"]["namespace"] for item in resources if item["kind"] != "Namespace"}, {"pocket-id"})
        deployment = next(item for item in resources if item["kind"] == "Deployment")
        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertEqual(deployment["spec"]["strategy"], {"type": "Recreate"})
        pod = deployment["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        container = pod["containers"][0]
        self.assertRegex(container["image"], r"^ghcr.io/pocket-id/pocket-id:v2\.19\.1@sha256:[a-f0-9]{64}$")
        env = {entry["name"]: entry for entry in container["env"]}
        self.assertEqual(env["ENCRYPTION_KEY"], {
            "name": "ENCRYPTION_KEY",
            "valueFrom": {"secretKeyRef": {"name": "pocket-id-secrets", "key": "ENCRYPTION_KEY"}},
        })
        self.assertEqual(env["DB_CONNECTION_STRING"]["value"], "/app/data/pocket-id.db")
        self.assertEqual(env["UPLOAD_PATH"]["value"], "/app/data/uploads")
        self.assertEqual(env["APP_URL"]["value"], "https://${POCKET_ID_HOST}")
        self.assertEqual(env["ALLOW_INSECURE_CALLBACK_URLS"]["value"], "false")
        self.assertEqual(container["volumeMounts"], [{"name": "data", "mountPath": "/app/data"}])
        self.assertEqual(pod["volumes"][0]["persistentVolumeClaim"]["claimName"], "pocket-id-data")
        for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
            self.assertEqual(container[probe]["exec"]["command"], ["/app/pocket-id", "healthcheck"])
        pvc = next(item for item in resources if item["kind"] == "PersistentVolumeClaim")
        self.assertEqual(pvc["spec"]["storageClassName"], "longhorn")
        self.assertEqual(pvc["spec"]["accessModes"], ["ReadWriteOnce"])
        self.assertEqual(pvc["spec"]["resources"]["requests"]["storage"], "1Gi")
        service = next(item for item in resources if item["kind"] == "Service")
        self.assertEqual(service["spec"]["selector"], deployment["spec"]["selector"]["matchLabels"])
        self.assertEqual(service["spec"]["ports"][0]["port"], 1411)
        ingress = next(item for item in resources if item["kind"] == "Ingress")
        self.assertEqual(ingress["spec"]["ingressClassName"], "traefik")
        self.assertEqual(ingress["metadata"]["annotations"]["cert-manager.io/cluster-issuer"], "acme-issuer")
        self.assertEqual(ingress["spec"]["tls"][0]["hosts"], ["${POCKET_ID_HOST}"])
        rule = ingress["spec"]["rules"][0]
        self.assertEqual(rule["host"], "${POCKET_ID_HOST}")
        self.assertEqual(rule["http"]["paths"][0]["backend"]["service"], {"name": service["metadata"]["name"], "port": {"name": "http"}})

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
            self.assertEqual(password["value"], "")
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
