package main

import (
	"fmt"
	"unsafe"

	"github.com/pion/rtp"
)

// #cgo pkg-config: libavcodec libavutil libswscale
// #include <libavcodec/avcodec.h>
// #include <libavutil/imgutils.h>
// #include <libswscale/swscale.h>
import "C"

var errAllFrameEmpty error = fmt.Errorf("all frames is empty")

type rtpDecoder interface {
	Decode(*rtp.Packet) ([][]byte, error)
}

func frameData(frame *C.AVFrame) **C.uint8_t {
	return (**C.uint8_t)(unsafe.Pointer(&frame.data[0]))
}

func frameLineSize(frame *C.AVFrame) *C.int {
	return (*C.int)(unsafe.Pointer(&frame.linesize[0]))
}

func pngEncoder(frame *C.AVFrame) ([]byte, error) {
	imageCodec := C.avcodec_find_encoder(C.AV_CODEC_ID_PNG)
	if imageCodec == nil {
		return nil, fmt.Errorf("avcodec_find_encoder() failed")
	}

	codecCtx := C.avcodec_alloc_context3(imageCodec)
	if codecCtx == nil {
		return nil, fmt.Errorf("avcodec_alloc_context3() failed")
	}
	defer C.avcodec_close(codecCtx)

	codecCtx.time_base = C.AVRational{1, 25}
	codecCtx.width = frame.width
	codecCtx.height = frame.height
	codecCtx.pix_fmt = C.AV_PIX_FMT_RGBA

	res := C.avcodec_open2(codecCtx, imageCodec, nil)
	if res < 0 {
		return nil, fmt.Errorf("avcodec_open2() failed")
	}

	packet := C.av_packet_alloc()
	if packet == nil {
		return nil, fmt.Errorf("av_packet_alloc() failed")
	}
	defer C.av_packet_free(&packet)

	res = C.avcodec_send_frame(codecCtx, frame)
	if res < 0 {
		return nil, nil
	}

	res = C.avcodec_receive_packet(codecCtx, packet)
	if res < 0 {
		return nil, nil
	}

	unsafeData := (*[1 << 30]uint8)(unsafe.Pointer(packet.data))[:packet.size:packet.size]
	data := make([]byte, len(unsafeData))
	copy(data, unsafeData)

	return data, nil
}

// h26xDecoder is a wrapper around FFmpeg's H264/H265 decoder.
type h26xDecoder struct {
	codecCtx *C.AVCodecContext
}

// initialize initializes a h26xDecoder.
func (d *h26xDecoder) initialize(codecName string) error {
	codecCode := uint32(C.AV_CODEC_ID_NONE)
	if codecName == "H264" {
		codecCode = uint32(C.AV_CODEC_ID_H264)
	} else if codecName == "H265" {
		codecCode = uint32(C.AV_CODEC_ID_H265)
	} else {
		return fmt.Errorf("codec not found")
	}

	codec := C.avcodec_find_decoder(codecCode)
	if codec == nil {
		return fmt.Errorf("avcodec_find_decoder() failed")
	}

	d.codecCtx = C.avcodec_alloc_context3(codec)
	if d.codecCtx == nil {
		return fmt.Errorf("avcodec_alloc_context3() failed")
	}

	res := C.avcodec_open2(d.codecCtx, codec, nil)
	if res < 0 {
		C.avcodec_close(d.codecCtx)
		return fmt.Errorf("avcodec_open2() failed")
	}

	return nil
}

// close closes the decoder.
func (d *h26xDecoder) close() {
	C.avcodec_close(d.codecCtx)
}

func (d *h26xDecoder) decode(nalu []byte) ([]byte, error) {
	nalu = append([]uint8{0x00, 0x00, 0x00, 0x01}, []uint8(nalu)...)

	packet := C.AVPacket{
		data: (*C.uint8_t)(C.CBytes(nalu)),
		size: C.int(len(nalu)),
	}
	defer C.free(unsafe.Pointer(packet.data))

	res := C.avcodec_send_packet(d.codecCtx, &packet)
	if res < 0 {
		return nil, nil
	}

	rawFrame := C.av_frame_alloc()
	if rawFrame == nil {
		return nil, fmt.Errorf("av_frame_alloc() failed")
	}
	defer C.av_frame_free(&rawFrame)

	res = C.avcodec_receive_frame(d.codecCtx, rawFrame)
	if res < 0 {
		return nil, nil
	}

	rgbaFrame := C.av_frame_alloc()
	if rgbaFrame == nil {
		return nil, fmt.Errorf("av_frame_alloc() failed")
	}
	defer C.av_frame_free(&rgbaFrame)

	rgbaFrame.format = C.AV_PIX_FMT_RGBA
	rgbaFrame.width = rawFrame.width
	rgbaFrame.height = rawFrame.height
	rgbaFrame.color_range = C.AVCOL_RANGE_JPEG

	res = C.av_frame_get_buffer(rgbaFrame, 1)
	if res < 0 {
		return nil, fmt.Errorf("av_frame_get_buffer() failed")
	}

	swsCtx := C.sws_getContext(
		rawFrame.width,
		rawFrame.height,
		C.AV_PIX_FMT_YUV420P,
		rgbaFrame.width,
		rgbaFrame.height,
		C.AV_PIX_FMT_RGBA,
		C.SWS_BILINEAR,
		nil,
		nil,
		nil,
	)
	if swsCtx == nil {
		return nil, fmt.Errorf("sws_getContext() failed")
	}
	defer C.sws_freeContext(swsCtx)

	res = C.sws_scale(
		swsCtx,
		frameData(rawFrame),
		frameLineSize(rawFrame),
		0,
		rawFrame.height,
		frameData(rgbaFrame),
		frameLineSize(rgbaFrame),
	)
	if res < 0 {
		return nil, fmt.Errorf("sws_scale() failed")
	}

	return pngEncoder(rgbaFrame)
}
