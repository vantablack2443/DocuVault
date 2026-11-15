{{- define "docuvault.name" -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}

{{- define "docuvault.labels" -}}
app.kubernetes.io/name: docuvault
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "docuvault.selector" -}}
app.kubernetes.io/name: docuvault
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: api
{{- end -}}

{{- define "docuvault.secretName" -}}
{{- .Values.existingSecret | default (printf "%s-env" (include "docuvault.name" .)) -}}
{{- end -}}

{{- define "docuvault.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end -}}

{{/* Shared by the migration init container and the API container. */}}
{{- define "docuvault.containerBase" -}}
image: {{ include "docuvault.image" . }}
imagePullPolicy: {{ .Values.image.pullPolicy }}
envFrom:
  - configMapRef:
      name: {{ include "docuvault.name" . }}-config
  - secretRef:
      name: {{ include "docuvault.secretName" . }}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
volumeMounts:
  - name: tmp
    mountPath: /tmp
{{- end -}}
