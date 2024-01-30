package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/png"
	_ "image/png"
	"log"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/bluenviron/gortsplib/v4"
	"github.com/bluenviron/gortsplib/v4/pkg/base"
)

var errMediaNotFound error = fmt.Errorf("media not found")

type OIDCClientCredentials struct {
	TokenURL string
	Username string
	Password string
	ClientID string
}

type AccessToken struct {
	AcsessToken string `json:"access_token"`
	TokenType   string `json:"token_type"`
	ExpiresIn   int    `json:"expires_in"`
}

type Camera struct {
	ID             string `json:"id"`
	RTSP_URL       string `json:"rtsp_url"`
	UpdateInterval int    `json:"update_interval"`
}

type infisicalConfig struct {
	url         string
	token       string
	secretKey   string
	environment string
}

type config struct {
	agentURL     string
	cameraURL    string
	heartbeatURL string
	credentials  OIDCClientCredentials
	heartbeat    time.Duration
}

func getAccessToken(ctx context.Context, credentials OIDCClientCredentials) (AccessToken, error) {
	data := url.Values{
		"grant_type": {"client_credentials"},
		"client_id":  {credentials.ClientID},
		"username":   {credentials.Username},
		"password":   {credentials.Password},
		"scope":      {"profile"},
	}

	body, err := httpPost(
		ctx,
		credentials.TokenURL,
		AccessToken{},
		"application/x-www-form-urlencoded",
		strings.NewReader(data.Encode()),
	)
	if err != nil {
		return AccessToken{}, fmt.Errorf("error getting Access Token: %w", err)
	}

	accessToken := AccessToken{}
	err = json.Unmarshal([]byte(body), &accessToken)
	if err != nil {
		return AccessToken{}, fmt.Errorf("error parsing body: %w", err)
	}

	return accessToken, err
}

func getSnapshot(ctx context.Context, rtspURL string) ([]byte, error) {
	client := gortsplib.Client{}

	u, err := base.ParseURL(rtspURL)
	if err != nil {
		return []byte{}, fmt.Errorf("error parsing RTSP URL: %w", err)
	}

	err = client.Start(u.Scheme, u.Host)
	if err != nil {
		return []byte{}, fmt.Errorf("error connecting to se server: %w", err)
	}
	defer client.Close()

	desc, _, err := client.Describe(u)
	if err != nil {
		return []byte{}, fmt.Errorf("error describing camera: %w", err)
	}

	imgch := make(chan image.Image)

	h264, err := addH264Decoder(&client, desc, imgch)
	if err != nil && !errors.Is(err, errMediaNotFound) {
		return []byte{}, fmt.Errorf("error adding H264 decoder: %w", err)
	}

	h265, err := addH265Decoder(&client, desc, imgch)
	if err != nil && !errors.Is(err, errMediaNotFound) {
		return []byte{}, fmt.Errorf("error adding H265 decoder: %w", err)
	}

	if h264 == nil && h265 == nil {
		return []byte{}, fmt.Errorf("media H264/H265 not found")
	}

	_, err = client.Play(nil)
	if err != nil {
		return nil, fmt.Errorf("error playing stream: %w", err)
	}

	select {
	case <-ctx.Done():
		return []byte{}, ctx.Err()
	case img := <-imgch:
		close(imgch)
		client.Close()
		buf := bytes.NewBuffer([]byte{})
		err := png.Encode(buf, img)

		return buf.Bytes(), err
	}
}

func processSnapshot(
	ctx context.Context,
	rtspURL string,
	snapshotURL string,
	accessToken AccessToken,
) error {
	imagech := make(chan []byte)
	errch := make(chan error)

	var image []byte

	go func() {
		image, err := getSnapshot(ctx, rtspURL)
		if err != nil {
			errch <- err
			return
		}
		imagech <- image
	}()

	select {
	case <-ctx.Done():
		return fmt.Errorf("timeout getting snapshot")
	case recimage := <-imagech:
		image = recimage
	case err := <-errch:
		return fmt.Errorf("error getting snapshot: %w", err)
	}

	rawBody := struct {
		ImageBase64 string `json:"image_base64"`
	}{
		ImageBase64: base64.StdEncoding.EncodeToString(image),
	}

	body, err := json.Marshal(rawBody)
	if err != nil {
		return fmt.Errorf("error encoding body: %w", err)
	}

	_, err = httpPost(
		context.Background(),
		snapshotURL,
		accessToken,
		"application/json",
		bytes.NewReader(body),
	)

	return err
}

func logSnapshot(ctx context.Context, camera Camera, snapshotURL string, accessToken AccessToken) {
	initialTime := time.Now()

	log.Printf("Realizando a captura da camera: %s\n", camera.ID)

	err := processSnapshot(ctx, camera.RTSP_URL, snapshotURL, accessToken)
	if err != nil {
		log.Printf("Erro ao realizar captura da camera %s: %s\n", camera.ID, err)
	} else {
		log.Printf("Captura realizada com sucesso: %s", camera.ID)
	}

	log.Printf(
		"Tempo de captura da camera %s: %.2fs\n",
		camera.ID,
		time.Since(initialTime).Seconds(),
	)
}

func runCameraSnapshot(
	ctx context.Context,
	cameraURL string,
	camera Camera,
	accessToken AccessToken,
) {
	log.Printf("Iniciando captura da camera: %s\n", camera.ID)

	defaultInterval := time.Second * time.Duration(camera.UpdateInterval)
	ticker := time.NewTicker(defaultInterval)
	snapshotURL := fmt.Sprintf("%s/%s/snapshot", cameraURL, camera.ID)
	ctxSnaphot, cancelSnapshot := context.WithTimeout(ctx, defaultInterval)
	wg := sync.WaitGroup{}

	wg.Add(1)
	go func() {
		logSnapshot(ctxSnaphot, camera, snapshotURL, accessToken)
		wg.Done()
	}()

	for {
		select {
		case <-ctx.Done():
			log.Printf("Finalizando a captura da camera: %s\n", camera.ID)
			cancelSnapshot()
			wg.Wait()
			return

		case <-ticker.C:
			cancelSnapshot()
			wg.Wait()
			ctxSnaphot, cancelSnapshot = context.WithTimeout(ctx, defaultInterval/2)
			wg.Add(1)
			go func() {
				logSnapshot(ctxSnaphot, camera, snapshotURL, accessToken)
				wg.Done()
			}()
		}
	}
}

func getCameras(ctx context.Context, cameraURL string, accessToken AccessToken) ([]Camera, error) {
	type apiData struct {
		Items []Camera `json:"items"`
		Total int      `json:"total"`
		Page  int      `json:"page"`
		Size  int      `json:"size"`
		Pages int      `json:"pages"`
	}
	data := apiData{}

	err := httpGet(ctx, cameraURL, accessToken, &data)
	if err != nil {
		return []Camera{}, fmt.Errorf("error getting cameras: %w", err)
	}

	return data.Items, err
}

func runCameras(
	ctx context.Context,
	wg *sync.WaitGroup,
	agentURL string,
	cameraURL string,
	credentials OIDCClientCredentials,
) error {
	accessToken, err := getAccessToken(ctx, credentials)
	if err != nil {
		return fmt.Errorf("error getting access token: %s\n", err)
	}

	cameras, err := getCameras(ctx, agentURL, accessToken)
	if err != nil {
		return fmt.Errorf("error getting cameras details: %w", err)
	}

	for _, camera := range cameras {
		camera := camera
		wg.Add(1)
		go func() {
			runCameraSnapshot(ctx, cameraURL, camera, accessToken)
			wg.Done()
		}()
	}

	return nil
}

func sendHeartbeat(
	ctx context.Context,
	heartbeatURL string,
	credentials OIDCClientCredentials,
	healthy bool,
) error {
	accessToken, err := getAccessToken(ctx, credentials)
	if err != nil {
		return fmt.Errorf("error getting access token: %s\n", err)
	}

	rawdata := struct {
		Healthy bool `json:"healthy"`
	}{
		Healthy: healthy,
	}

	data, err := json.Marshal(rawdata)
	if err != nil {
		return fmt.Errorf("error creating JSON body: %w", err)
	}

	_, err = httpPost(ctx, heartbeatURL, accessToken, "application/json", bytes.NewReader(data))

	return err
}

func main() {
	config, err := getConfig()
	if err != nil {
		log.Printf("%s", fmt.Errorf("error getting config: %w", err))
		return
	}

	osSignal := make(chan os.Signal, 1)
	log.Println("Esperando sinal de interrupção")
	signal.Notify(osSignal, syscall.SIGINT, syscall.SIGHUP, syscall.SIGTERM)

	wg := sync.WaitGroup{}
	ticker := time.NewTicker(config.heartbeat)

	for {
		ctxCameras, cancelCameras := context.WithCancel(context.Background())

		err := runCameras(
			ctxCameras,
			&wg,
			config.agentURL,
			config.cameraURL,
			config.credentials,
		)
		if err != nil {
			log.Printf("Erro ao rodar as cameras: %s\n", err)
		}

		err = sendHeartbeat(
			ctxCameras,
			config.heartbeatURL,
			config.credentials,
			err == nil,
		)
		if err != nil {
			log.Printf("Error sending heartbeat: %s\n", err)
		}

		select {
		case <-osSignal:
			log.Println("Esperando as capturas serem finalizadas")
			cancelCameras()
			wg.Wait()
			log.Println("Capturas finalizadas com sucesso")

			return
		case <-ticker.C:
			log.Println("Esperando as capturas serem finalizadas")
			cancelCameras()
			wg.Wait()
		}
	}
}
