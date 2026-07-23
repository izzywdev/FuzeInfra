// Package notify implements best-effort, non-blocking notifiers used by the
// Contabo cluster-autoscaler provider to warn a human operator immediately
// before it provisions a new elastic VPS (see
// internal/provider/scale.go's NodeGroupIncreaseSize).
//
// Two delivery mechanisms are implemented — Emailer (SMTP) and Telegram (the
// Bot API) — selected by cmd/server/main.go's loadConfig (Telegram takes
// precedence over email when both are enabled).
//
// This exists purely as an early-warning signal — the authoritative
// anti-runaway safeguard is the prefix-count hard cap in scale.go, not this
// notification. Delivery must therefore NEVER gate, delay, or fail a
// scale-up: Notify swallows every error internally (logging it) rather than
// returning one, and each notifier is disabled by default (Config.Enabled /
// Config.TelegramEnabled default to the zero value false) so an
// unconfigured/misconfigured endpoint can never affect a fresh deploy.
package notify

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/smtp"
	"strings"
	"time"
)

// Config configures both notifiers. All fields are sourced from env vars by
// cmd/server/main.go's loadConfig:
//
//   - NOTIFY_EMAIL_ENABLED      (bool)             -> Enabled
//   - NOTIFY_SMTP_HOST          (string)           -> SMTPHost
//   - NOTIFY_SMTP_PORT          (string)           -> SMTPPort
//   - NOTIFY_SMTP_USER          (string, optional) -> SMTPUser
//   - NOTIFY_SMTP_PASS          (string, optional) -> SMTPPass
//   - NOTIFY_EMAIL_FROM         (string)           -> From
//   - NOTIFY_EMAIL_TO           (string)           -> To (comma-separated)
//   - NOTIFY_TELEGRAM_ENABLED   (bool)             -> TelegramEnabled
//   - NOTIFY_TELEGRAM_BOT_TOKEN (string)           -> TelegramBotToken
//   - NOTIFY_TELEGRAM_CHAT_ID   (string)           -> TelegramChatID
type Config struct {
	Enabled  bool
	SMTPHost string
	SMTPPort string
	SMTPUser string
	SMTPPass string
	From     string
	To       string

	TelegramEnabled  bool
	TelegramBotToken string
	TelegramChatID   string
}

// Notifier sends a best-effort notification. Implementations must never
// propagate a delivery failure as an error to the caller — see
// Emailer.Notify / Telegram.Notify.
type Notifier interface {
	Notify(subject, body string)
}

// dialTimeout bounds the SMTP TCP connect so a stalled/unreachable mail
// relay can never hang the calling goroutine indefinitely.
const dialTimeout = 5 * time.Second

// Emailer is the stdlib net/smtp-backed Notifier used in production.
type Emailer struct {
	cfg Config

	// now/dial/newClient are overridable in tests so Notify's full happy and
	// failure paths can be exercised without a real network dial.
	now       func() time.Time
	dial      func(network, addr string, timeout time.Duration) (net.Conn, error)
	newClient func(conn net.Conn, host string) (smtpClient, error)
}

// smtpClient is the subset of *smtp.Client used by send, extracted as an
// interface so tests can inject a fake without opening a real socket.
type smtpClient interface {
	Auth(a smtp.Auth) error
	Mail(from string) error
	Rcpt(to string) error
	Data() (io.WriteCloser, error)
	Quit() error
	Close() error
}

// realSMTPClient adapts *smtp.Client to the smtpClient interface.
type realSMTPClient struct{ *smtp.Client }

func (r realSMTPClient) Data() (io.WriteCloser, error) { return r.Client.Data() }

// New builds an Emailer backed by real SMTP over a real TCP dial.
func New(cfg Config) *Emailer {
	return &Emailer{
		cfg: cfg,
		now: time.Now,
		dial: func(network, addr string, timeout time.Duration) (net.Conn, error) {
			return net.DialTimeout(network, addr, timeout)
		},
		newClient: func(conn net.Conn, host string) (smtpClient, error) {
			c, err := smtp.NewClient(conn, host)
			if err != nil {
				return nil, err
			}
			return realSMTPClient{c}, nil
		},
	}
}

// Notify sends subject/body as a best-effort email if e.cfg.Enabled, logging
// (never returning) any delivery failure. A nil receiver or Enabled=false is
// a silent no-op — callers do not need to nil-check before calling Notify.
func (e *Emailer) Notify(subject, body string) {
	if e == nil || !e.cfg.Enabled {
		return
	}
	if err := e.send(subject, body); err != nil {
		log.Printf("notify: email send failed (non-blocking, scaling proceeds regardless): %v", err)
	}
}

func splitAddrs(s string) []string {
	var out []string
	for _, part := range strings.Split(s, ",") {
		p := strings.TrimSpace(part)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

func (e *Emailer) send(subject, body string) error {
	if e.cfg.SMTPHost == "" {
		return fmt.Errorf("NOTIFY_SMTP_HOST is empty")
	}
	from := e.cfg.From
	if from == "" {
		return fmt.Errorf("NOTIFY_EMAIL_FROM is empty")
	}
	to := splitAddrs(e.cfg.To)
	if len(to) == 0 {
		return fmt.Errorf("NOTIFY_EMAIL_TO is empty")
	}

	addr := e.cfg.SMTPHost
	if e.cfg.SMTPPort != "" {
		addr = net.JoinHostPort(e.cfg.SMTPHost, e.cfg.SMTPPort)
	}

	conn, err := e.dial("tcp", addr, dialTimeout)
	if err != nil {
		return fmt.Errorf("dial smtp %s: %w", addr, err)
	}
	// The client takes ownership of conn on success; Close is still safe to
	// call twice (net.Conn documents a second Close as an error, which we
	// ignore here since we only care about the first, real failure path).
	client, err := e.newClient(conn, e.cfg.SMTPHost)
	if err != nil {
		conn.Close()
		return fmt.Errorf("smtp handshake with %s: %w", addr, err)
	}
	defer client.Close()

	if e.cfg.SMTPUser != "" {
		auth := smtp.PlainAuth("", e.cfg.SMTPUser, e.cfg.SMTPPass, e.cfg.SMTPHost)
		if err := client.Auth(auth); err != nil {
			return fmt.Errorf("smtp auth: %w", err)
		}
	}
	if err := client.Mail(from); err != nil {
		return fmt.Errorf("smtp MAIL FROM: %w", err)
	}
	for _, addr := range to {
		if err := client.Rcpt(addr); err != nil {
			return fmt.Errorf("smtp RCPT TO %s: %w", addr, err)
		}
	}
	w, err := client.Data()
	if err != nil {
		return fmt.Errorf("smtp DATA: %w", err)
	}
	if _, err := w.Write(buildMessage(from, to, subject, body, e.now())); err != nil {
		w.Close()
		return fmt.Errorf("smtp write message: %w", err)
	}
	if err := w.Close(); err != nil {
		return fmt.Errorf("smtp close message: %w", err)
	}
	return client.Quit()
}

// buildMessage renders a minimal RFC 5322 message. now is injected (rather
// than calling time.Now directly) so tests can assert on the Date header
// deterministically.
func buildMessage(from string, to []string, subject, body string, now time.Time) []byte {
	var b strings.Builder
	b.WriteString("From: " + from + "\r\n")
	b.WriteString("To: " + strings.Join(to, ", ") + "\r\n")
	b.WriteString("Subject: " + subject + "\r\n")
	b.WriteString("Date: " + now.Format(time.RFC1123Z) + "\r\n")
	b.WriteString("Content-Type: text/plain; charset=\"utf-8\"\r\n")
	b.WriteString("\r\n")
	b.WriteString(body)
	b.WriteString("\r\n")
	return []byte(b.String())
}

// telegramHTTPTimeout bounds the whole Telegram Bot API HTTP round trip
// (connect + write + read) so a stalled/unreachable API can never hang the
// calling goroutine indefinitely — same intent as Emailer's dialTimeout.
const telegramHTTPTimeout = 5 * time.Second

// defaultTelegramAPIBase is the production Telegram Bot API base URL.
const defaultTelegramAPIBase = "https://api.telegram.org"

// Telegram is an HTTP-backed Notifier that posts to the Telegram Bot API's
// sendMessage endpoint (https://core.telegram.org/bots/api#sendmessage).
// Same failure-tolerance contract as Emailer: Notify never returns an error
// or panics; every failure is logged and swallowed so an unreachable/
// misconfigured bot can never affect a scale-up.
type Telegram struct {
	cfg    Config
	client *http.Client

	// TelegramAPIBase overrides the Telegram Bot API base URL. Defaults to
	// defaultTelegramAPIBase (https://api.telegram.org); tests point this at
	// an httptest.Server so no real network call is ever made.
	TelegramAPIBase string
}

// NewTelegram builds a Telegram notifier backed by a real HTTP client with a
// telegramHTTPTimeout bound.
func NewTelegram(cfg Config) *Telegram {
	return &Telegram{
		cfg:             cfg,
		client:          &http.Client{Timeout: telegramHTTPTimeout},
		TelegramAPIBase: defaultTelegramAPIBase,
	}
}

// Notify posts subject/body as a best-effort Telegram message if
// t.cfg.TelegramEnabled, logging (never returning) any delivery failure. A
// nil receiver or TelegramEnabled=false is a silent no-op — callers do not
// need to nil-check before calling Notify.
func (t *Telegram) Notify(subject, body string) {
	if t == nil || !t.cfg.TelegramEnabled {
		return
	}
	if err := t.send(subject, body); err != nil {
		log.Printf("notify: telegram send failed (non-blocking, scaling proceeds regardless): %v", err)
	}
}

// telegramSendMessageReq is the minimal request body for the Bot API's
// sendMessage method: https://core.telegram.org/bots/api#sendmessage.
type telegramSendMessageReq struct {
	ChatID string `json:"chat_id"`
	Text   string `json:"text"`
}

func (t *Telegram) send(subject, body string) error {
	if t.cfg.TelegramBotToken == "" {
		return fmt.Errorf("NOTIFY_TELEGRAM_BOT_TOKEN is empty")
	}
	if t.cfg.TelegramChatID == "" {
		return fmt.Errorf("NOTIFY_TELEGRAM_CHAT_ID is empty")
	}

	text := subject
	if body != "" {
		text = subject + "\n\n" + body
	}

	payload, err := json.Marshal(telegramSendMessageReq{ChatID: t.cfg.TelegramChatID, Text: text})
	if err != nil {
		return fmt.Errorf("marshal telegram sendMessage payload: %w", err)
	}

	base := t.TelegramAPIBase
	if base == "" {
		base = defaultTelegramAPIBase
	}
	url := fmt.Sprintf("%s/bot%s/sendMessage", base, t.cfg.TelegramBotToken)

	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("build telegram sendMessage request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	client := t.client
	if client == nil {
		client = &http.Client{Timeout: telegramHTTPTimeout}
	}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("telegram sendMessage request: %w", err)
	}
	defer resp.Body.Close()
	// Drain the body so the connection can be reused; the response content
	// itself is not needed since delivery failures are only ever logged.
	_, _ = io.Copy(io.Discard, resp.Body)

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("telegram sendMessage: unexpected status %d", resp.StatusCode)
	}
	return nil
}
