package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const (
	defaultModel                = "gpt-5.4-mini"
	defaultOpenAIModel          = defaultModel
	defaultOpenRouterModel      = "openai/" + defaultModel
	defaultCodexModel           = "gpt-5.5"
	defaultCodexReasoningEffort = "none"
)

type provider struct {
	Name            string
	BaseURL         string
	Token           string
	Model           string
	ReasoningEffort string
}

type llmResult struct {
	Text  string
	Model string
}

type args struct {
	Dry           bool
	PrintCommand  bool
	Model         string
	Provider      string
	OpenRouterKey string
	Explain       bool
	Request       []string
}

func loadJSON(path string) map[string]any {
	b, err := os.ReadFile(path)
	if err != nil {
		return map[string]any{}
	}
	var data map[string]any
	if err := json.Unmarshal(b, &data); err != nil {
		return map[string]any{}
	}
	return data
}

func homeDir() string {
	home, _ := os.UserHomeDir()
	return home
}

func codexAuthPath() string {
	if v := os.Getenv("CODEX_HOME"); v != "" {
		return filepath.Join(v, "auth.json")
	}
	return filepath.Join(homeDir(), ".codex", "auth.json")
}

func configPaths() []string {
	var paths []string
	if v := os.Getenv("CLAI_CONFIG"); v != "" {
		paths = append(paths, v)
	}
	cwd, _ := os.Getwd()
	xdg := os.Getenv("XDG_CONFIG_HOME")
	if xdg == "" {
		xdg = filepath.Join(homeDir(), ".config")
	}
	return append(paths,
		filepath.Join(cwd, ".clai.json"),
		filepath.Join(homeDir(), ".clai.json"),
		filepath.Join(xdg, "clai", "config.json"),
	)
}

func loadConfig() map[string]any {
	for _, p := range configPaths() {
		data := loadJSON(p)
		if len(data) > 0 {
			return data
		}
	}
	return map[string]any{}
}

func configStr(config map[string]any, key string) string {
	if v, ok := config[key].(string); ok {
		return strings.TrimSpace(v)
	}
	return ""
}

func modelFor(config map[string]any, providerName, fallback string) string {
	if v := configStr(config, providerName+"_model"); v != "" {
		return v
	}
	if v := configStr(config, "model"); v != "" {
		return v
	}
	return fallback
}

func codexToken() string {
	auth := loadJSON(codexAuthPath())
	if tokens, ok := auth["tokens"].(map[string]any); ok {
		if v, ok := tokens["access_token"].(string); ok {
			return v
		}
	}
	if v, ok := auth["OPENAI_API_KEY"].(string); ok {
		return strings.TrimSpace(v)
	}
	return ""
}

func resolveProvider(a args) (provider, error) {
	config := loadConfig()
	providerName := a.Provider
	if providerName == "" {
		providerName = configStr(config, "provider")
	}
	if providerName == "" {
		providerName = "auto"
	}
	openRouterKey := firstNonEmpty(a.OpenRouterKey, os.Getenv("OPENROUTER_API_KEY"), configStr(config, "openrouter_api_key"))
	openAIKey := firstNonEmpty(os.Getenv("OPENAI_API_KEY"), configStr(config, "openai_api_key"))
	codexTok := codexToken()
	codexReasoning := firstNonEmpty(configStr(config, "codex_reasoning_effort"), defaultCodexReasoningEffort)
	_, codexErr := exec.LookPath("codex")
	hasCodex := codexErr == nil

	switch providerName {
	case "auto":
		if codexTok != "" && hasCodex {
			return provider{"codex", "codex", "", firstNonEmpty(a.Model, modelFor(config, "codex", defaultCodexModel)), codexReasoning}, nil
		}
		if openRouterKey != "" {
			return provider{"openrouter", "https://openrouter.ai/api/v1/chat/completions", openRouterKey, firstNonEmpty(a.Model, modelFor(config, "openrouter", defaultOpenRouterModel)), ""}, nil
		}
		if openAIKey != "" {
			return provider{"openai", "https://api.openai.com/v1/responses", openAIKey, firstNonEmpty(a.Model, modelFor(config, "openai", defaultOpenAIModel)), ""}, nil
		}
	case "openai":
		if openAIKey != "" {
			return provider{"openai", "https://api.openai.com/v1/responses", openAIKey, firstNonEmpty(a.Model, modelFor(config, "openai", defaultOpenAIModel)), ""}, nil
		}
	case "codex":
		if codexTok != "" && hasCodex {
			return provider{"codex", "codex", "", firstNonEmpty(a.Model, modelFor(config, "codex", defaultCodexModel)), codexReasoning}, nil
		}
	case "openrouter":
		if openRouterKey != "" {
			return provider{"openrouter", "https://openrouter.ai/api/v1/chat/completions", openRouterKey, firstNonEmpty(a.Model, modelFor(config, "openrouter", defaultOpenRouterModel)), ""}, nil
		}
	}
	return provider{}, errors.New("No LLM credentials found. Login with Codex so ~/.codex/auth.json exists, or set OPENROUTER_API_KEY / OPENAI_API_KEY, or put keys in ~/.clai.json.")
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

type message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

func buildPrompt(request string) []message {
	cwd, _ := os.Getwd()
	shell := firstNonEmpty(os.Getenv("SHELL"), "/bin/sh")
	system := "Convert the user's request into one robust shell command. " +
		"Return JSON only, no markdown, with keys: command, explanation. " +
		"The command must fit the user's OS, shell, and current directory, and be a single line. " +
		"The command will already run in the current directory; do not include a cd to it. " +
		"Prefer correctness over brevity. " +
		"For destructive commands over multiple targets, use canonical machine-readable sources, " +
		"filter exact leaf targets before acting, skip empty/meta/container names, quote targets, " +
		"avoid xargs, and use explicit loops/checks. " +
		"If the active/current item might be deleted, first switch to an existing kept safe item; fail only if none exists. " +
		"Do not include comments, markdown, or multiple alternatives."
	user := fmt.Sprintf("Current working directory: %s\nOS: %s/%s\nShell: %s\nUser request: %s", cwd, runtime.GOOS, runtime.GOARCH, shell, request)
	return []message{{"system", system}, {"user", user}}
}

func callCodexCLI(p provider, request string) (llmResult, error) {
	schema := map[string]any{
		"type": "object",
		"properties": map[string]any{
			"command":     map[string]string{"type": "string"},
			"explanation": map[string]string{"type": "string"},
		},
		"required":             []string{"command", "explanation"},
		"additionalProperties": false,
	}
	messages := buildPrompt(request)
	var parts []string
	for _, m := range messages {
		parts = append(parts, strings.ToUpper(m.Role)+": "+m.Content)
	}
	prompt := strings.Join(parts, "\n\n") + "\n\nImportant: do not execute any command. Only return the JSON object."

	td, err := os.MkdirTemp("", "clai-")
	if err != nil {
		return llmResult{}, err
	}
	defer os.RemoveAll(td)
	schemaPath := filepath.Join(td, "schema.json")
	outPath := filepath.Join(td, "out.json")
	b, _ := json.Marshal(schema)
	if err := os.WriteFile(schemaPath, b, 0o600); err != nil {
		return llmResult{}, err
	}

	cmdArgs := []string{"--ask-for-approval", "never", "-c", fmt.Sprintf("model_reasoning_effort=\"%s\"", firstNonEmpty(p.ReasoningEffort, defaultCodexReasoningEffort)), "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-rules", "--sandbox", "read-only", "--color", "never", "--output-schema", schemaPath, "--output-last-message", outPath}
	if p.Model != "" {
		cmdArgs = append(cmdArgs, "--model", p.Model)
	}
	cmdArgs = append(cmdArgs, prompt)
	cmd := exec.Command("codex", cmdArgs...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return llmResult{}, fmt.Errorf("Codex CLI failed: %s", strings.TrimSpace(string(out)))
	}
	text, err := os.ReadFile(outPath)
	if err != nil {
		return llmResult{}, fmt.Errorf("Codex CLI did not write an output message: %w", err)
	}
	return llmResult{string(text), parseCodexModel(string(out), p)}, nil
}

func parseCodexModel(stdout string, p provider) string {
	if p.Model != "" {
		return p.Model
	}
	for _, line := range strings.Split(stdout, "\n") {
		if strings.HasPrefix(line, "model:") {
			return strings.TrimSpace(strings.TrimPrefix(line, "model:"))
		}
	}
	return "codex-default"
}

func httpJSON(url string, headers map[string]string, payload map[string]any) (map[string]any, error) {
	body, _ := json.Marshal(payload)
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("LLM request failed: %w", err)
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("LLM request failed (%d): %s", resp.StatusCode, string(respBody))
	}
	var data map[string]any
	if err := json.Unmarshal(respBody, &data); err != nil {
		return nil, err
	}
	return data, nil
}

func callLLM(p provider, request string) (llmResult, error) {
	if p.Name == "codex" {
		return callCodexCLI(p, request)
	}
	messages := buildPrompt(request)
	headers := map[string]string{"Authorization": "Bearer " + p.Token}
	if p.Name == "openrouter" {
		headers["X-Title"] = "clai"
		data, err := httpJSON(p.BaseURL, headers, map[string]any{"model": p.Model, "messages": messages, "temperature": 0})
		if err != nil {
			return llmResult{}, err
		}
		choices, _ := data["choices"].([]any)
		if len(choices) > 0 {
			if ch, ok := choices[0].(map[string]any); ok {
				if msg, ok := ch["message"].(map[string]any); ok {
					if content, ok := msg["content"].(string); ok {
						return llmResult{content, firstNonEmpty(p.Model, defaultOpenRouterModel)}, nil
					}
				}
			}
		}
		return llmResult{}, fmt.Errorf("could not read LLM response: %.1000s", mustJSON(data))
	}

	data, err := httpJSON(p.BaseURL, headers, map[string]any{"model": p.Model, "input": messages, "text": map[string]any{"format": map[string]string{"type": "json_object"}}})
	if err != nil {
		return llmResult{}, err
	}
	if text, ok := data["output_text"].(string); ok {
		return llmResult{text, firstNonEmpty(p.Model, defaultOpenAIModel)}, nil
	}
	var chunks []string
	if output, ok := data["output"].([]any); ok {
		for _, item := range output {
			im, _ := item.(map[string]any)
			content, _ := im["content"].([]any)
			for _, c := range content {
				cm, _ := c.(map[string]any)
				if t, ok := cm["text"].(string); ok {
					chunks = append(chunks, t)
				}
			}
		}
	}
	if len(chunks) > 0 {
		return llmResult{strings.Join(chunks, ""), firstNonEmpty(p.Model, defaultOpenAIModel)}, nil
	}
	return llmResult{}, fmt.Errorf("could not read LLM response: %.1000s", mustJSON(data))
}

func mustJSON(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}

func extractCommand(text string) (string, string, error) {
	raw := strings.TrimSpace(text)
	if strings.HasPrefix(raw, "```") {
		raw = strings.Trim(raw, "`")
		if strings.HasPrefix(raw, "json") {
			raw = strings.TrimSpace(raw[4:])
		}
	}
	var data map[string]any
	var command, explanation string
	if err := json.Unmarshal([]byte(raw), &data); err == nil {
		command = strings.TrimSpace(fmt.Sprint(data["command"]))
		explanation = strings.TrimSpace(fmt.Sprint(data["explanation"]))
	} else {
		lines := strings.Split(raw, "\n")
		command = strings.TrimSpace(lines[0])
	}
	command = strings.Join(strings.Fields(strings.ReplaceAll(command, "\n", " ")), " ")
	if command == "" {
		return "", "", fmt.Errorf("LLM did not return a command: %q", text)
	}
	return command, explanation, nil
}

func parseArgs(argv []string) (args, error) {
	if len(argv) == 0 {
		printHelp()
		os.Exit(0)
	}
	var a args
	for i := 0; i < len(argv); i++ {
		s := argv[i]
		switch s {
		case "--":
			a.Request = append(a.Request, argv[i+1:]...)
			return a, nil
		case "--help", "-h":
			printHelp()
			os.Exit(0)
		case "--dry", "-n":
			a.Dry = true
		case "--print":
			a.PrintCommand = true
		case "--explain":
			a.Explain = true
		case "--model":
			i++
			if i >= len(argv) {
				return a, errors.New("--model requires a value")
			}
			a.Model = argv[i]
		case "--provider":
			i++
			if i >= len(argv) {
				return a, errors.New("--provider requires a value")
			}
			if !contains([]string{"auto", "codex", "openai", "openrouter"}, argv[i]) {
				return a, errors.New("--provider must be one of: auto, codex, openai, openrouter")
			}
			a.Provider = argv[i]
		case "--openrouter-key":
			i++
			if i >= len(argv) {
				return a, errors.New("--openrouter-key requires a value")
			}
			a.OpenRouterKey = argv[i]
		default:
			if strings.HasPrefix(s, "--model=") {
				a.Model = strings.TrimPrefix(s, "--model=")
			} else if strings.HasPrefix(s, "--provider=") {
				v := strings.TrimPrefix(s, "--provider=")
				if !contains([]string{"auto", "codex", "openai", "openrouter"}, v) {
					return a, errors.New("--provider must be one of: auto, codex, openai, openrouter")
				}
				a.Provider = v
			} else if strings.HasPrefix(s, "--openrouter-key=") {
				a.OpenRouterKey = strings.TrimPrefix(s, "--openrouter-key=")
			} else {
				a.Request = append(a.Request, argv[i:]...)
				return a, nil
			}
		}
	}
	return a, nil
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}

func printHelp() {
	fmt.Println("Usage: clai [--dry|-n] [--print] [--model MODEL] [--provider auto|codex|openai|openrouter] [--openrouter-key KEY] [--explain] request...")
	fmt.Println("Translate natural language into a shell command and run it.")
}

func main() {
	flag.CommandLine.SetOutput(io.Discard)
	a, err := parseArgs(os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, "clai:", err)
		os.Exit(2)
	}
	request := strings.TrimSpace(strings.Join(a.Request, " "))
	if request == "" {
		printHelp()
		return
	}
	p, err := resolveProvider(a)
	if err != nil {
		fmt.Fprintln(os.Stderr, "clai:", err)
		os.Exit(1)
	}
	result, err := callLLM(p, request)
	if err != nil {
		fmt.Fprintln(os.Stderr, "clai:", err)
		os.Exit(1)
	}
	command, explanation, err := extractCommand(result.Text)
	if err != nil {
		fmt.Fprintln(os.Stderr, "clai:", err)
		os.Exit(1)
	}
	if a.Explain && explanation != "" {
		fmt.Fprintln(os.Stderr, "# "+explanation)
	}
	if a.PrintCommand || a.Explain {
		fmt.Fprintln(os.Stderr, "# "+command)
	}
	if a.Dry {
		fmt.Println(command)
		return
	}
	shell := firstNonEmpty(os.Getenv("SHELL"), "/bin/sh")
	cmd := exec.Command(shell, "-c", command)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		if exit, ok := err.(*exec.ExitError); ok {
			os.Exit(exit.ExitCode())
		}
		fmt.Fprintln(os.Stderr, "clai:", err)
		os.Exit(1)
	}
}
