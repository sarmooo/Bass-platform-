{{- define "bass.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "bass.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "bass.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "bass.labels" -}}
helm.sh/chart: {{ include "bass.chart" . }}
{{ include "bass.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "bass.selectorLabels" -}}
app.kubernetes.io/name: {{ include "bass.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "bass.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "bass.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* The Secret name holding jwtSecret + databaseUrl */}}
{{- define "bass.secretName" -}}
{{- if .Values.secret.existingSecret -}}
{{- .Values.secret.existingSecret -}}
{{- else -}}
{{- printf "%s-secret" (include "bass.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/* Common env block shared by the Deployment and the migration Job */}}
{{- define "bass.env" -}}
- name: BASS_JWT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "bass.secretName" . }}
      key: jwtSecret
- name: BASS_DB_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "bass.secretName" . }}
      key: databaseUrl
- name: BASS_RATE_LIMIT_PER_MIN
  value: {{ .Values.config.rateLimitPerMin | quote }}
- name: BASS_LOG_JSON
  value: {{ ternary "1" "0" .Values.config.logJson | quote }}
{{- end -}}
