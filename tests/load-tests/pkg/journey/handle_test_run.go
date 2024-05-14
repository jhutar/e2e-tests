package journey

import "fmt"
import "strings"
import "time"

import logging "github.com/redhat-appstudio/e2e-tests/tests/load-tests/pkg/logging"

import appstudioApi "github.com/redhat-appstudio/application-api/api/v1alpha1"
import framework "github.com/redhat-appstudio/e2e-tests/pkg/framework"
import utils "github.com/redhat-appstudio/e2e-tests/pkg/utils"
import pipeline "github.com/tektoncd/pipeline/pkg/apis/pipeline/v1"


func ValidateSnapshotCreation(f *framework.Framework, namespace, compName string) (string, error) {
	interval := time.Second * 20
	timeout := time.Minute * 30
	var snap *appstudioApi.Snapshot

	// TODO It would be much better to watch this resource for a condition
	err := utils.WaitUntilWithInterval(func() (done bool, err error) {
		snap, err = f.AsKubeDeveloper.IntegrationController.GetSnapshot("", "", compName, namespace)
		if err != nil {
			logging.Logger.Debug("Unable to get created Snapshot for component %s in namespace %s: %v", compName, namespace, err)
			return false, nil
		}
		return true, nil
	}, interval, timeout)

	return snap.Name, err
}

func ValidateTestPipelineRunCreation(f *framework.Framework, namespace, itsName, snapName string) error {
	interval := time.Second * 20
	timeout := time.Minute * 30

	// TODO It would be much better to watch this resource for a condition
	err := utils.WaitUntilWithInterval(func() (done bool, err error) {
		_, err = f.AsKubeDeveloper.IntegrationController.GetIntegrationPipelineRun(itsName, snapName, namespace)
		if err != nil {
			logging.Logger.Debug("Unable to get created test PipelineRun for integration test pipeline %s in namespace %s: %v", itsName, namespace, err)
			return false, nil
		}
		return true, nil
	}, interval, timeout)

	return err
}

func ValidateTestPipelineRun(f *framework.Framework, namespace, itsName, snapName string) error {
	interval:= time.Second * 20
	timeout:= time.Minute * 60
	var pr *pipeline.PipelineRun

	// TODO It would be much better to watch this resource for a condition
	err := utils.WaitUntilWithInterval(func() (done bool, err error) {
		pr, err = f.AsKubeDeveloper.IntegrationController.GetIntegrationPipelineRun(itsName, snapName, namespace)
		if err != nil {
			logging.Logger.Debug("Unable to get created test PipelineRun for integration test pipeline %s in namespace %s: %v", snapName, namespace, err)
			return false, nil
		}

		// Check if there are some conditions
		if len(pr.Status.Conditions) == 0 {
			logging.Logger.Debug("PipelineRun for integration test pipeline %s in namespace %s lacks status conditions", snapName, namespace)
			return false, nil
		}

		// Check right condition status
		for _, condition := range pr.Status.Conditions {
			if (strings.HasPrefix(string(condition.Type), "Error") || strings.HasSuffix(string(condition.Type), "Error")) && condition.Status == "True" {
				return false, fmt.Errorf("PipelineRun for integration test pipeline %s in namespace %s is in error state: %+v", snapName, namespace, condition)
			}
			if condition.Type == "Succeeded" && condition.Status == "True" {
				return true, nil
			}
		}

		logging.Logger.Debug("Still waiting for test pipeline run for integration test pipeline %s in namespace %s", snapName, namespace)
		return false, nil
	}, interval, timeout)

	return err
}

func HandleTest(ctx *PerComponentContext) error {
	if ! ctx.ParentContext.Opts.WaitPipelines || ! ctx.ParentContext.Opts.WaitIntegrationTestsPipelines{
		return nil
	}

	var err error
	var snapName string

	logging.Logger.Debug("Creating test pipeline run for component %s in namespace %s", ctx.ComponentName, ctx.ParentContext.Namespace)

	snapName, err = ValidateSnapshotCreation(ctx.Framework, ctx.ParentContext.Namespace, ctx.ComponentName)
	if err != nil {
		return logging.Logger.Fail(80, "Snapshot failed creation: %v", err)
	}

	ctx.SnapshotName = snapName

	err = ValidateTestPipelineRunCreation(ctx.Framework, ctx.ParentContext.Namespace, ctx.ParentContext.IntegrationTestScenarioName, snapName)
	if err != nil {
		return logging.Logger.Fail(81, "Test Pipeline Run failed creation: %v", err)
	}

	err = ValidateTestPipelineRun(ctx.Framework, ctx.ParentContext.Namespace, ctx.ParentContext.IntegrationTestScenarioName, snapName)
	if err != nil {
		return logging.Logger.Fail(82, "Test Pipeline Run failed run: %v", err)
	}

	return nil
}