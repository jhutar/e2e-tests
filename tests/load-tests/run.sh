#!/bin/bash
export MY_GITHUB_ORG GITHUB_TOKEN

TEKTON_PERF_PROFILE_CPU_PERIOD=${TEKTON_PERF_PROFILE_CPU_PERIOD:-300}

output_dir="${OUTPUT_DIR:-.}"
USER_PREFIX=${USER_PREFIX:-testuser}

## Enable CPU profiling in Tekton
if [ "${TEKTON_PERF_ENABLE_CPU_PROFILING:-}" == "true" ]; then
    echo "Starting CPU profiling with pprof"
    for p in $(oc get pods -n openshift-pipelines -l app=tekton-pipelines-controller -o name); do
        pod="${p##*/}"
        file="tekton-pipelines-controller.$pod.cpu-profile"
        oc exec -n openshift-pipelines "$p" -- bash -c "curl -SsL --max-time $((TEKTON_PERF_PROFILE_CPU_PERIOD + 10)) localhost:8008/debug/pprof/profile?seconds=${TEKTON_PERF_PROFILE_CPU_PERIOD} | base64" | base64 -d >"$output_dir/$file.pprof" &
        echo $! >"$output_dir/$file.pid"
    done
    p=$(oc get pods -n tekton-results -l app.kubernetes.io/name=tekton-results-watcher -o name)
    pod="${p##*/}"
    file=tekton-results-watcher.$pod.cpu-profile
    oc exec -n tekton-results "$p" -c watcher -- bash -c "curl -SsL --max-time $((TEKTON_PERF_PROFILE_CPU_PERIOD + 10)) localhost:8008/debug/pprof/profile?seconds=${TEKTON_PERF_PROFILE_CPU_PERIOD} | base64" | base64 -d >"$output_dir/$file.pprof" &
    echo $! >"$output_dir/$file.pid"
fi

## Enable memory profiling in Tekton
if [ "${TEKTON_PERF_ENABLE_MEMORY_PROFILING:-}" == "true" ]; then
    file=tekton-pipelines-controller.memory-profile
    echo "Starting memory profiling of Tekton controller with pprof"
    for p in $(oc get pods -n openshift-pipelines -l app=tekton-pipelines-controller -o name); do
        pod="${p##*/}"
        file="tekton-pipelines-controller.$pod.memory-profile"
        oc exec -n openshift-pipelines "$p" -- bash -c "curl -SsL --max-time $((TEKTON_PERF_PROFILE_CPU_PERIOD + 10)) localhost:8008/debug/pprof/heap?seconds=${TEKTON_PERF_PROFILE_CPU_PERIOD} | base64" | base64 -d >"$output_dir/$file.pprof" &
        echo $! >"$output_dir/$file.pid"
    done
    echo "Starting memory profiling of Tekton results watcher with pprof"
    for p in $(oc get pods -n tekton-results -l app.kubernetes.io/name=tekton-results-watcher -o name); do
        pod="${p##*/}"
        file=tekton-results-watcher.$pod.memory-profile
        oc exec -n tekton-results "$p" -c watcher -- bash -c "curl -SsL --max-time $((TEKTON_PERF_PROFILE_CPU_PERIOD + 10)) localhost:8008/debug/pprof/heap?seconds=${TEKTON_PERF_PROFILE_CPU_PERIOD} | base64" | base64 -d >"$output_dir/$file.pprof" &
        echo $! >"$output_dir/$file.pid"
    done
fi

## Switch KubeScheduler Debugging on
if [ -n "$KUBE_SCHEDULER_LOG_LEVEL" ]; then
    echo "Checking KubeScheduler log level"
    if [ "$(oc get KubeScheduler cluster -o jsonpath="{.spec.logLevel}")" == "$KUBE_SCHEDULER_LOG_LEVEL" ]; then
        echo "KubeScheduler log level is already at $KUBE_SCHEDULER_LOG_LEVEL level"
    else
        echo "Setting KubeScheduler log level to $KUBE_SCHEDULER_LOG_LEVEL"
        oc patch KubeScheduler cluster --type=json -p='[{"op": "add", "path": "/spec/logLevel", "value": "'"$KUBE_SCHEDULER_LOG_LEVEL"'"}]'
        echo "Waiting for kube scheduler to start NodeInstallerProgressing"
        oc wait --for=condition=NodeInstallerProgressing kubescheduler/cluster -n openshift-kube-scheduler --timeout=300s
    fi
    echo "Waiting for all kube scheduler pods to finish NodeInstallerProgressing"
    oc wait --for=condition=NodeInstallerProgressing=False kubescheduler/cluster -n openshift-kube-scheduler --timeout=900s
    echo "All kube scheduler pods are now at log level $KUBE_SCHEDULER_LOG_LEVEL, starting to capture logs"
    oc logs -f -n openshift-kube-scheduler --prefix -l app=openshift-kube-scheduler --tail=-1 2>&1 >"$output_dir/openshift-kube-scheduler.log" &
    KUBE_SCHEDULER_LOG_PID=$!
fi

## Run the actual load test
options=""
[[ -n "${PIPELINE_IMAGE_PULL_SECRETS:-}" ]] && for s in $PIPELINE_IMAGE_PULL_SECRETS; do options="$options --pipeline-image-pull-secrets $s"; done
date -Ins --utc >started
go run loadtest.go \
    --applications-count "${APPLICATIONS_COUNT:-1}" \
    --build-pipeline-selector-bundle "${BUILD_PIPELINE_SELECTOR_BUNDLE:-}" \
    --component-repo "${COMPONENT_REPO:-https://github.com/nodeshift-starters/devfile-sample}" \
    --component-repo-container-context "${COMPONENT_REPO_CONTAINER_CONTEXT:-/}" \
    --component-repo-container-file "${COMPONENT_REPO_CONTAINER_FILE:-Dockerfile}" \
    --component-repo-revision "${COMPONENT_REPO_REVISION:-main}" \
    --components-count "${COMPONENTS_COUNT:-1}" \
    --concurrency "${CONCURRENCY:-1}" \
    --journey-duration "${JOURNEY_DURATION:-1h}" \
    --journey-repeats "${JOURNEY_REPEATS:-1}" \
    --log-"${LOGGING_LEVEL:-info}" \
    --pipeline-repo-templating="${PIPELINE_REPO_TEMPLATING:-false}" \
    --output-dir "${OUTPUT_DIR:-.}" \
    --purge="${PURGE:-true}" \
    --quay-repo "${QUAY_REPO:-stonesoup_perfscale}" \
    --test-scenario-git-url "${TEST_SCENARIO_GIT_URL:-https://github.com/konflux-ci/integration-examples.git}" \
    --test-scenario-path-in-repo "${TEST_SCENARIO_PATH_IN_REPO:-pipelines/integration_resolver_pipeline_pass.yaml}" \
    --test-scenario-revision "${TEST_SCENARIO_REVISION:-main}" \
    --username "$USER_PREFIX" \
    --waitintegrationtestspipelines="${WAIT_INTEGRATION_TESTS:-true}" \
    --waitpipelines="${WAIT_PIPELINES:-true}" \
    $options \
    2>&1 | tee load-test.log

# Capture and exit if there are unexpected errors in loadtest.go
LOADTEST_EXIT_STATUS=${PIPESTATUS[0]}
if [ ${LOADTEST_EXIT_STATUS} -ne 0 ]; then
    echo "[$(date --utc -Ins)] loadtest.go exited with non-zero (${LOADTEST_EXIT_STATUS}) status code."
    exit 1
fi

date -Ins --utc >ended

## Finish Tekton profiling
if [ "${TEKTON_PERF_ENABLE_CPU_PROFILING:-}" == "true" ] || [ "${TEKTON_PERF_ENABLE_MEMORY_PROFILING:-}" == "true" ]; then
    echo "Waiting for the Tekton profiling to finish up to ${TEKTON_PERF_PROFILE_CPU_PERIOD}s"
    for pid_file in $(find $output_dir -name 'tekton*.pid'); do
        wait "$(cat "$pid_file")"
        rm -rvf "$pid_file"
    done
    echo "Getting Tekton controller goroutine dump"
    for p in $(oc get pods -n openshift-pipelines -l app=tekton-pipelines-controller -o name); do
        pod="${p##*/}"
        for i in 0 1 2; do
            file="tekton-pipelines-controller.$pod.goroutine-dump-$i"
            oc exec -n tekton-results "$p" -- bash -c "curl -SsL localhost:8008/debug/pprof/goroutine?debug=$i | base64" | base64 -d >"$output_dir/$file.pprof"
        done
    done
    echo "Getting Tekton results watcher goroutine dump"
    for p in $(oc get pods -n tekton-results -l app.kubernetes.io/name=tekton-results-watcher -o name); do
        pod="${p##*/}"
        for i in 0 1 2; do
            file="tekton-results-watcher.$pod.goroutine-dump-$i"
            oc exec -n tekton-results "$p" -c watcher -- bash -c "curl -SsL localhost:8008/debug/pprof/goroutine?debug=$i | base64" | base64 -d >"$output_dir/$file.pprof"
        done
    done
fi

## Stop collecting KubeScheduler log
if [ -n "$KUBE_SCHEDULER_LOG_LEVEL" ]; then
    echo "Killing kube collector log collector"
    kill "$KUBE_SCHEDULER_LOG_PID"
fi
