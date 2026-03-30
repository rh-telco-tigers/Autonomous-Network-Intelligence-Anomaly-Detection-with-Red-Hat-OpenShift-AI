SHELL := /bin/zsh

.PHONY: kustomize-demo validate-python repo-tree

kustomize-demo:
	kustomize build k8s/overlays/demo

validate-python:
	python3 -m compileall services ai

repo-tree:
	rg --files .

