package notify

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/smtp"
	"strings"
	"testing"
	"time"
)

// fakeConn is a minimal net.Conn stand-in; the emailer never actually reads
// or writes it in these tests because newClient is faked out separately, but
// Close is overridden (rather than relying on the nil-embedded net.Conn) so
// the newClient-failure path — which calls conn.Close() — cannot panic.
type fakeConn struct{ net.Conn }

func (fakeConn) Close() error { return nil }

// fakeSMTPClient is a test double for smtpClient (see notify.go) that lets
// each call be scripted to fail, so send's error paths are exercised without
// a real SMTP dial.
type fakeSMTPClient struct {
	authErr  error
	mailErr  error
	rcptErr  error
	dataErr  error
	quitErr  error
	closed   bool
	authedAs string
	mailFrom string
	rcptTo   []string
	written  bytes.Buffer
}

func (f *fakeSMTPClient) Auth(a smtp.Auth) error {
	f.authedAs = "authed"
	return f.authErr
}
func (f *fakeSMTPClient) Mail(from string) error {
	f.mailFrom = from
	return f.mailErr
}
func (f *fakeSMTPClient) Rcpt(to string) error {
	f.rcptTo = append(f.rcptTo, to)
	return f.rcptErr
}
func (f *fakeSMTPClient) Data() (io.WriteCloser, error) {
	if f.dataErr != nil {
		return nil, f.dataErr
	}
	return nopWriteCloser{&f.written}, nil
}
func (f *fakeSMTPClient) Quit() error  { return f.quitErr }
func (f *fakeSMTPClient) Close() error { f.closed = true; return nil }

type nopWriteCloser struct{ *bytes.Buffer }

func (nopWriteCloser) Close() error { return nil }

// newTestEmailer builds an Emailer whose dial/newClient are faked so no
// network I/O ever happens, with fsc as the scripted SMTP client returned by
// newClient (unless dialErr/newClientErr are set to short-circuit earlier).
func newTestEmailer(cfg Config, fsc *fakeSMTPClient, dialErr, newClientErr error, dialCalls, newClientCalls *int) *Emailer {
	return &Emailer{
		cfg: cfg,
		now: func() time.Time { return time.Date(2026, 7, 23, 12, 0, 0, 0, time.UTC) },
		dial: func(network, addr string, timeout time.Duration) (net.Conn, error) {
			if dialCalls != nil {
				*dialCalls++
			}
			if dialErr != nil {
				return nil, dialErr
			}
			return fakeConn{}, nil
		},
		newClient: func(conn net.Conn, host string) (smtpClient, error) {
			if newClientCalls != nil {
				*newClientCalls++
			}
			if newClientErr != nil {
				return nil, newClientErr
			}
			return fsc, nil
		},
	}
}

func baseCfg() Config {
	return Config{
		Enabled:  true,
		SMTPHost: "smtp.example.com",
		SMTPPort: "587",
		From:     "ca@example.com",
		To:       "ops@example.com,oncall@example.com",
	}
}

// TestNotify_DisabledIsNoOp verifies the mandatory gate: with Enabled=false,
// Notify must never dial the network at all, and must return immediately
// (no panic, no error surfaced — there is nothing TO surface since Notify
// has no return value).
func TestNotify_DisabledIsNoOp(t *testing.T) {
	cfg := baseCfg()
	cfg.Enabled = false

	var dialCalls, newClientCalls int
	e := newTestEmailer(cfg, &fakeSMTPClient{}, nil, nil, &dialCalls, &newClientCalls)

	e.Notify("subject", "body")

	if dialCalls != 0 {
		t.Fatalf("Notify with Enabled=false: want 0 dial calls, got %d", dialCalls)
	}
	if newClientCalls != 0 {
		t.Fatalf("Notify with Enabled=false: want 0 newClient calls, got %d", newClientCalls)
	}
}

// TestNotify_NilReceiverIsNoOp verifies callers never need to nil-check a
// *Emailer before calling Notify (mirrors how provider.Config.Notifier can
// be a nil interface when NOTIFY_EMAIL_ENABLED was never wired up).
func TestNotify_NilReceiverIsNoOp(t *testing.T) {
	var e *Emailer
	// Must not panic.
	e.Notify("subject", "body")
}

// TestNotify_DialFailureIsTolerated verifies that when the SMTP dial itself
// fails (e.g. relay unreachable), Notify swallows the error — it must not
// panic or block the caller, matching the "failure-tolerant" requirement so
// an email problem can never wedge scaling.
func TestNotify_DialFailureIsTolerated(t *testing.T) {
	cfg := baseCfg()
	var dialCalls, newClientCalls int
	e := newTestEmailer(cfg, &fakeSMTPClient{}, errors.New("connection refused"), nil, &dialCalls, &newClientCalls)

	// Must return normally (no panic) despite the dial failure.
	e.Notify("subject", "body")

	if dialCalls != 1 {
		t.Fatalf("want 1 dial attempt, got %d", dialCalls)
	}
	if newClientCalls != 0 {
		t.Fatalf("want newClient never called after a dial failure, got %d calls", newClientCalls)
	}
}

// TestNotify_SendFailureIsTolerated verifies that a failure further into the
// SMTP conversation (e.g. RCPT TO rejected) is also swallowed rather than
// propagated or panicking.
func TestNotify_SendFailureIsTolerated(t *testing.T) {
	cfg := baseCfg()
	fsc := &fakeSMTPClient{rcptErr: errors.New("550 mailbox unavailable")}
	e := newTestEmailer(cfg, fsc, nil, nil, nil, nil)

	e.Notify("subject", "body")

	if !fsc.closed {
		t.Fatal("want the SMTP client Close()'d even after a mid-conversation failure")
	}
}

// TestSend_HappyPath verifies a fully successful send exercises every SMTP
// step in order and writes a well-formed message.
func TestSend_HappyPath(t *testing.T) {
	cfg := baseCfg()
	cfg.SMTPUser = "ca-bot"
	cfg.SMTPPass = "secret"
	fsc := &fakeSMTPClient{}
	e := newTestEmailer(cfg, fsc, nil, nil, nil, nil)

	if err := e.send("provisioning fuzeinfra-elastic-abcd1234", "instance=fuzeinfra-elastic-abcd1234\ncurrent=2\ntarget=3\n"); err != nil {
		t.Fatalf("send: unexpected error: %v", err)
	}

	if fsc.authedAs == "" {
		t.Fatal("want Auth() called when SMTPUser is set")
	}
	if fsc.mailFrom != "ca@example.com" {
		t.Fatalf("Mail(from) = %q, want ca@example.com", fsc.mailFrom)
	}
	wantTo := []string{"ops@example.com", "oncall@example.com"}
	if len(fsc.rcptTo) != len(wantTo) {
		t.Fatalf("Rcpt calls = %v, want %v", fsc.rcptTo, wantTo)
	}
	for i, addr := range wantTo {
		if fsc.rcptTo[i] != addr {
			t.Fatalf("Rcpt[%d] = %q, want %q", i, fsc.rcptTo[i], addr)
		}
	}
	msg := fsc.written.String()
	if !strings.Contains(msg, "Subject: provisioning fuzeinfra-elastic-abcd1234") {
		t.Fatalf("message missing expected Subject header: %q", msg)
	}
	if !strings.Contains(msg, "instance=fuzeinfra-elastic-abcd1234") {
		t.Fatalf("message missing expected body: %q", msg)
	}
	if !fsc.closed {
		t.Fatal("want the SMTP client Close()'d after Quit()")
	}
}

// TestSend_NoAuthWhenUserUnset verifies Auth() is skipped entirely when
// SMTPUser is empty (many local/relay-only SMTP setups accept unauthenticated
// mail), rather than calling PlainAuth with empty credentials.
func TestSend_NoAuthWhenUserUnset(t *testing.T) {
	cfg := baseCfg() // SMTPUser left empty
	fsc := &fakeSMTPClient{}
	e := newTestEmailer(cfg, fsc, nil, nil, nil, nil)

	if err := e.send("subject", "body"); err != nil {
		t.Fatalf("send: unexpected error: %v", err)
	}
	if fsc.authedAs != "" {
		t.Fatal("want Auth() NOT called when SMTPUser is empty")
	}
}

// TestSend_MissingConfigFieldsError verifies send fails fast (an error the
// caller — Notify — will log and swallow) when required delivery fields are
// blank, rather than silently sending a malformed message.
func TestSend_MissingConfigFieldsError(t *testing.T) {
	cases := []struct {
		name string
		mut  func(*Config)
	}{
		{"empty host", func(c *Config) { c.SMTPHost = "" }},
		{"empty from", func(c *Config) { c.From = "" }},
		{"empty to", func(c *Config) { c.To = "" }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := baseCfg()
			tc.mut(&cfg)
			e := newTestEmailer(cfg, &fakeSMTPClient{}, nil, nil, nil, nil)
			if err := e.send("subject", "body"); err == nil {
				t.Fatal("want an error, got nil")
			}
		})
	}
}

// --- Telegram -------------------------------------------------------------

func telegramCfg() Config {
	return Config{
		TelegramEnabled:  true,
		TelegramBotToken: "123456:ABC-DEF",
		TelegramChatID:   "-1001234567890",
	}
}

// TestTelegramNotify_DisabledIsNoOp verifies the mandatory gate: with
// TelegramEnabled=false, Notify must never make an HTTP request at all.
func TestTelegramNotify_DisabledIsNoOp(t *testing.T) {
	var calls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	cfg := telegramCfg()
	cfg.TelegramEnabled = false
	tg := NewTelegram(cfg)
	tg.TelegramAPIBase = srv.URL

	tg.Notify("subject", "body")

	if calls != 0 {
		t.Fatalf("Notify with TelegramEnabled=false: want 0 HTTP calls, got %d", calls)
	}
}

// TestTelegramNotify_NilReceiverIsNoOp verifies callers never need to
// nil-check a *Telegram before calling Notify (mirrors Emailer's contract).
func TestTelegramNotify_NilReceiverIsNoOp(t *testing.T) {
	var tg *Telegram
	// Must not panic.
	tg.Notify("subject", "body")
}

// TestTelegramNotify_HappyPath verifies a fully successful send POSTs
// chat_id+text as JSON to <base>/bot<token>/sendMessage.
func TestTelegramNotify_HappyPath(t *testing.T) {
	type gotReq struct {
		method      string
		path        string
		contentType string
		body        telegramSendMessageReq
	}
	var got gotReq
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got.method = r.Method
		got.path = r.URL.Path
		got.contentType = r.Header.Get("Content-Type")
		if err := json.NewDecoder(r.Body).Decode(&got.body); err != nil {
			t.Errorf("decoding request body: %v", err)
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	cfg := telegramCfg()
	tg := NewTelegram(cfg)
	tg.TelegramAPIBase = srv.URL

	tg.Notify("provisioning fuzeinfra-elastic-abcd1234", "instance=fuzeinfra-elastic-abcd1234\ncurrent=2\ntarget=3\n")

	if got.method != http.MethodPost {
		t.Errorf("method = %q, want POST", got.method)
	}
	wantPath := "/bot123456:ABC-DEF/sendMessage"
	if got.path != wantPath {
		t.Errorf("path = %q, want %q", got.path, wantPath)
	}
	if !strings.Contains(got.contentType, "application/json") {
		t.Errorf("Content-Type = %q, want application/json", got.contentType)
	}
	if got.body.ChatID != cfg.TelegramChatID {
		t.Errorf("chat_id = %q, want %q", got.body.ChatID, cfg.TelegramChatID)
	}
	if !strings.Contains(got.body.Text, "provisioning fuzeinfra-elastic-abcd1234") {
		t.Errorf("text missing subject: %q", got.body.Text)
	}
	if !strings.Contains(got.body.Text, "instance=fuzeinfra-elastic-abcd1234") {
		t.Errorf("text missing body: %q", got.body.Text)
	}
}

// TestTelegramNotify_NonSuccessStatusIsTolerated verifies that a non-2xx
// response from the Telegram API (e.g. 401 bad token, 400 bad chat) is
// swallowed rather than propagated or panicking.
func TestTelegramNotify_NonSuccessStatusIsTolerated(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"ok":false,"description":"Unauthorized"}`))
	}))
	defer srv.Close()

	cfg := telegramCfg()
	tg := NewTelegram(cfg)
	tg.TelegramAPIBase = srv.URL

	// Must return normally (no panic) despite the non-2xx response.
	tg.Notify("subject", "body")
}

// TestTelegramNotify_DialFailureIsTolerated verifies that when the HTTP
// request itself fails (e.g. unreachable API base), Notify swallows the
// error — it must not panic or block the caller.
func TestTelegramNotify_DialFailureIsTolerated(t *testing.T) {
	cfg := telegramCfg()
	tg := NewTelegram(cfg)
	// Port 0 on the loopback resolves but nothing listens there, so the
	// dial itself fails without needing a real unreachable host (and
	// without depending on external network/DNS being unavailable in CI).
	tg.TelegramAPIBase = "http://127.0.0.1:0"

	// Must return normally (no panic) despite the dial failure.
	tg.Notify("subject", "body")
}

// TestTelegramSend_MissingConfigFieldsError verifies send fails fast (an
// error Notify will log and swallow) when required delivery fields are
// blank, rather than silently posting a malformed request.
func TestTelegramSend_MissingConfigFieldsError(t *testing.T) {
	cases := []struct {
		name string
		mut  func(*Config)
	}{
		{"empty bot token", func(c *Config) { c.TelegramBotToken = "" }},
		{"empty chat id", func(c *Config) { c.TelegramChatID = "" }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := telegramCfg()
			tc.mut(&cfg)
			tg := NewTelegram(cfg)
			if err := tg.send("subject", "body"); err == nil {
				t.Fatal("want an error, got nil")
			}
		})
	}
}

// TestTelegramSend_DefaultAPIBaseWhenUnset verifies send falls back to
// defaultTelegramAPIBase (rather than an invalid empty-base URL) when
// TelegramAPIBase is left as the zero value — covers a Telegram struct built
// without NewTelegram (e.g. a future direct literal). No real network call
// is made: the client's transport is faked out via http.Client.Transport so
// the request is inspected instead of dialed.
func TestTelegramSend_DefaultAPIBaseWhenUnset(t *testing.T) {
	var gotURL string
	rt := roundTripFunc(func(r *http.Request) (*http.Response, error) {
		gotURL = r.URL.String()
		return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader("{}"))}, nil
	})
	tg := &Telegram{cfg: telegramCfg(), client: &http.Client{Transport: rt}}

	if err := tg.send("subject", "body"); err != nil {
		t.Fatalf("send: unexpected error: %v", err)
	}
	wantPrefix := defaultTelegramAPIBase + "/bot"
	if !strings.HasPrefix(gotURL, wantPrefix) {
		t.Fatalf("request URL = %q, want prefix %q", gotURL, wantPrefix)
	}
}

// roundTripFunc adapts a function to http.RoundTripper so tests can fake the
// entire HTTP transport without opening a real socket.
type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
