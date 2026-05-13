/*
 * SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

package dynamo

import (
	"fmt"
	"strings"

	"github.com/ai-dynamo/dynamo/deploy/operator/api/v1beta1"
	commonconsts "github.com/ai-dynamo/dynamo/deploy/operator/internal/consts"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
)

// WorkerDefaults implements ComponentDefaults for Worker components
type WorkerDefaults struct {
	*BaseComponentDefaults
}

func NewWorkerDefaults() *WorkerDefaults {
	return &WorkerDefaults{&BaseComponentDefaults{}}
}

func (w *WorkerDefaults) GetBaseContainer(context ComponentContext) (corev1.Container, error) {
	container := w.getCommonContainer(context)

	// Add system port
	container.Ports = []corev1.ContainerPort{
		{
			Protocol:      corev1.ProtocolTCP,
			Name:          commonconsts.DynamoSystemPortName,
			ContainerPort: int32(commonconsts.DynamoSystemPort),
		},
		{
			Protocol:      corev1.ProtocolTCP,
			Name:          commonconsts.DynamoNixlPortName,
			ContainerPort: int32(commonconsts.DynamoNixlPort),
		},
	}

	container.LivenessProbe = &corev1.Probe{
		ProbeHandler: corev1.ProbeHandler{
			HTTPGet: &corev1.HTTPGetAction{
				Path: "/live",
				Port: intstr.FromString(commonconsts.DynamoSystemPortName),
			},
		},
		PeriodSeconds:    5,
		TimeoutSeconds:   4, // TimeoutSeconds should be < PeriodSeconds
		FailureThreshold: 1, // Note this default FailureThreshold is 3, with 1 a single failure will restart Pod
	}

	// ReadinessProbe in Dynamo worker context doesn't determine that the worker is ready to receive traffic
	// Since worker registration is done through external KvStore and Transport does not use Kubernetes Service
	// Still important for external depencies that rely on Pod Readiness
	container.ReadinessProbe = &corev1.Probe{
		ProbeHandler: corev1.ProbeHandler{
			HTTPGet: &corev1.HTTPGetAction{
				Path: "/health",
				Port: intstr.FromString(commonconsts.DynamoSystemPortName),
			},
		},
		PeriodSeconds:    10,
		TimeoutSeconds:   4,
		FailureThreshold: 3,
	}

	container.StartupProbe = &corev1.Probe{
		ProbeHandler: corev1.ProbeHandler{
			HTTPGet: &corev1.HTTPGetAction{
				Path: "/live",
				Port: intstr.FromString(commonconsts.DynamoSystemPortName),
			},
		},
		PeriodSeconds:    10,
		TimeoutSeconds:   5,
		FailureThreshold: 720, // 10s * 720 = 7200s = 2h
	}

	container.Env = append(container.Env, []corev1.EnvVar{
		{
			Name:  "DYN_SYSTEM_ENABLED",
			Value: "true",
		},
		{
			Name:  "DYN_SYSTEM_USE_ENDPOINT_HEALTH_STATUS",
			Value: "[\"generate\"]",
		},
		{
			Name:  "DYN_SYSTEM_PORT",
			Value: fmt.Sprintf("%d", commonconsts.DynamoSystemPort),
		},
		{
			Name:  "DYN_HEALTH_CHECK_ENABLED",
			Value: "false",
		},
		{
			Name:  "NIXL_TELEMETRY_ENABLE",
			Value: "n",
		},
		{
			Name:  "NIXL_TELEMETRY_EXPORTER",
			Value: "prometheus",
		},
		{
			Name:  "NIXL_TELEMETRY_PROMETHEUS_PORT",
			Value: fmt.Sprintf("%d", commonconsts.DynamoNixlPort),
		},
		{
			Name:  "DYN_FORWARDPASS_METRIC_PORT",
			Value: fmt.Sprintf("%d", commonconsts.DynamoFPMBasePort),
		},
	}...)

	if context.WorkerHashSuffix != "" {
		container.Env = append(container.Env, []corev1.EnvVar{
			{
				Name:  commonconsts.DynamoNamespaceWorkerSuffixEnvVar,
				Value: context.WorkerHashSuffix,
			},
		}...)
	}

	return container, nil
}

const (
	topologyVolumeName = "topology-labels"
	topologyMountPath  = "/etc/dynamo/topology"
)

// TopologyLabelCopyInitContainer returns an init container that reads a node
// label via the K8s API and patches it onto the pod. The Downward API volume
// then projects the label value into a file the runtime reads.
func TopologyLabelCopyInitContainer(policy *v1beta1.KvTransferPolicy) corev1.Container {
	labelKey := policy.LabelKey

	script := fmt.Sprintf(`set -e
APISERVER=https://kubernetes.default.svc
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
CACERT=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt
AUTH="Authorization: Bearer $TOKEN"

LABEL_KEY="%s"
NODE_JSON=$(curl -fsSL --cacert $CACERT -H "$AUTH" "$APISERVER/api/v1/nodes/$NODE_NAME")
LABEL_VALUE=$(echo "$NODE_JSON" | sed -n "s|.*\"${LABEL_KEY}\": *\"\([^\"]*\)\".*|\1|p")

if [ -z "$LABEL_VALUE" ]; then
  echo "ERROR: node label '$LABEL_KEY' not found on node $NODE_NAME" >&2
  exit 1
fi

NAMESPACE=$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace)
PATCH="{\"metadata\":{\"labels\":{\"${LABEL_KEY}\":\"${LABEL_VALUE}\"}}}"
curl -fsSL --cacert $CACERT -H "$AUTH" -H "Content-Type: application/strategic-merge-patch+json" \
  -X PATCH -d "$PATCH" "$APISERVER/api/v1/namespaces/$NAMESPACE/pods/$POD_NAME" > /dev/null

echo "Copied node label $LABEL_KEY=$LABEL_VALUE to pod $POD_NAME"
`, labelKey)

	return corev1.Container{
		Name:    "copy-topology-label",
		Image:   "bitnami/kubectl:latest",
		Command: []string{"sh", "-c", script},
		Env: []corev1.EnvVar{
			{
				Name: "POD_NAME",
				ValueFrom: &corev1.EnvVarSource{
					FieldRef: &corev1.ObjectFieldSelector{FieldPath: "metadata.name"},
				},
			},
			{
				Name: "NODE_NAME",
				ValueFrom: &corev1.EnvVarSource{
					FieldRef: &corev1.ObjectFieldSelector{FieldPath: "spec.nodeName"},
				},
			},
		},
	}
}

// TopologyLabelVolume returns a Downward API volume that projects the pod
// label (copied from the node by the init container) into a file. Unlike
// env var fieldRefs, volumes reflect live label updates.
func TopologyLabelVolume(policy *v1beta1.KvTransferPolicy) corev1.Volume {
	domain := strings.ToLower(string(policy.Domain))
	return corev1.Volume{
		Name: topologyVolumeName,
		VolumeSource: corev1.VolumeSource{
			DownwardAPI: &corev1.DownwardAPIVolumeSource{
				Items: []corev1.DownwardAPIVolumeFile{
					{
						Path: domain,
						FieldRef: &corev1.ObjectFieldSelector{
							FieldPath: fmt.Sprintf("metadata.labels['%s']", policy.LabelKey),
						},
					},
				},
			},
		},
	}
}

// TopologyLabelVolumeMount returns the volume mount for the topology label volume.
func TopologyLabelVolumeMount() corev1.VolumeMount {
	return corev1.VolumeMount{
		Name:      topologyVolumeName,
		MountPath: topologyMountPath,
		ReadOnly:  true,
	}
}

// WorkerTopologyEnvVars returns env vars that signal topology awareness to
// the worker runtime. The topology value is read from the Downward API volume
// file at /etc/dynamo/topology/{domain}.
func WorkerTopologyEnvVars(policy *v1beta1.KvTransferPolicy) []corev1.EnvVar {
	domain := strings.ToLower(string(policy.Domain))
	return []corev1.EnvVar{
		{
			Name:  commonconsts.EnvTopologyEnabled,
			Value: "true",
		},
		{
			Name:  commonconsts.EnvTopologyPrefix + "MOUNT_PATH",
			Value: topologyMountPath,
		},
		{
			Name:  commonconsts.EnvTopologyPrefix + "DOMAIN",
			Value: domain,
		},
	}
}
