int castkms_capture_queue_buffer_ioctl(struct drm_device *dev, void *data,
				       struct drm_file *file_priv)
{
	struct drm_castkms_capture_queue_buffer *args = data;
	struct castkms_file *file_state = file_priv->driver_priv;
	struct castkms_capture_uapi_stream *uapi_stream;
	struct castkms_capture_uapi_request *uapi_request;
	struct castkms_capture_authority *authority;
	struct castkms_capture_buffer *buffer;
	enum castkms_capture_sync_mode sync_mode;
	int ret;

	if (args->reserved ||
	    (args->flags != DRM_CASTKMS_CAPTURE_QUEUE_IMPLICIT_SYNC &&
	     args->flags != DRM_CASTKMS_CAPTURE_QUEUE_EXPLICIT_SYNC))
		return -EINVAL;
	if (args->flags == DRM_CASTKMS_CAPTURE_QUEUE_IMPLICIT_SYNC &&
	    (args->ready_point || args->reuse_point))
		return -EINVAL;

	uapi_request = kzalloc_obj(*uapi_request);
	if (!uapi_request)
		return -ENOMEM;
	uapi_request->event.base.type = DRM_CASTKMS_CAPTURE_EVENT_FRAME;
	uapi_request->event.base.length = sizeof(uapi_request->event);
	uapi_request->request.complete = castkms_capture_uapi_request_complete;
	uapi_request->request.ready_point = args->ready_point;
	uapi_request->request.reuse_point = args->reuse_point;
	uapi_request->dev = dev;
	uapi_request->user_data = args->user_data;
	uapi_request->stream_id = args->stream_id;
	uapi_request->buffer_id = args->buffer_id;

	ret = drm_event_reserve_init(dev, file_priv, &uapi_request->pending,
				     &uapi_request->event.base);
	if (ret) {
		kfree(uapi_request);
		return ret;
	}

	ret = castkms_grant_begin(file_priv, NULL,
				  CASTKMS_CAPTURE_AUTHORITY_CAPTURE_PIXELS,
				  &authority);
	if (ret)
		goto out_cancel_event;

	mutex_lock(&file_state->capture_lock);
	uapi_stream = xa_load(&file_state->capture_streams, args->stream_id);
	if (!uapi_stream) {
		ret = -ENOENT;
		goto out_unlock;
	}
	ret = castkms_capture_uapi_validate_stream(dev, file_priv, uapi_stream,
						   authority);
	if (ret)
		goto out_unlock;
	ret = castkms_capture_stream_validate_mode(
		uapi_stream->stream, args->mode_generation);
	if (ret)
		goto out_unlock;

	buffer = xa_load(&uapi_stream->buffers, args->buffer_id);
	if (!buffer) {
		ret = -ENOENT;
		goto out_unlock;
	}
	sync_mode = args->flags == DRM_CASTKMS_CAPTURE_QUEUE_EXPLICIT_SYNC ?
		CASTKMS_CAPTURE_SYNC_EXPLICIT : CASTKMS_CAPTURE_SYNC_IMPLICIT;
	if (castkms_capture_buffer_sync_mode(buffer) != sync_mode) {
		ret = -EINVAL;
		goto out_unlock;
	}

	/* PREPARING pins the stream and buffer across authority release. */
	ret = castkms_capture_buffer_prepare_submit(buffer);

out_unlock:
	mutex_unlock(&file_state->capture_lock);
	castkms_grant_end(authority);
	if (ret)
		goto out_cancel_event;

	/* Fence preparation may wait on a reservation object. */
	ret = castkms_capture_buffer_submit_prepared(
		buffer, &uapi_request->request);
	if (ret)
		goto out_cancel_event;

	return 0;

out_cancel_event:
	drm_event_cancel_free(dev, &uapi_request->pending);
	return ret;
}
