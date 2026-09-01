// SPDX-License-Identifier: GPL-2.0-only

#include <linux/build_bug.h>
#include <linux/dma-fence.h>
#include <linux/err.h>
#include <linux/kernel.h>
#include <linux/kref.h>
#include <linux/ktime.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/xarray.h>

#include <drm/castkms_drm.h>
#include <drm/drm_crtc.h>
#include <drm/drm_drv.h>
#include <drm/drm_file.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_framebuffer.h>
#include <drm/drm_syncobj.h>

#include "castkms_capture.h"
#include "castkms_capture_authority.h"
#include "castkms_capture_uapi.h"
#include "castkms_file.h"
#include "castkms_grant.h"
#include "castkms_limits.h"
#include "castkms_output.h"

struct castkms_capture_uapi_stream {
	struct kref ref;
	struct castkms_capture_authority_resource resource;
	struct castkms_capture_authority *authority;
	struct castkms_file *file_state;
	struct castkms_capture_stream *stream;
	struct xarray buffers;
	u32 stream_id;
	bool published;
};

/*
 * @pending must remain first: DRM event cleanup frees the pending-event
 * address after delivery or cancellation.
 */
struct castkms_capture_uapi_request {
	struct drm_pending_event pending;
	struct drm_event_castkms_capture_frame event;
	struct castkms_capture_request request;
	struct drm_device *dev;
	u64 user_data;
	u32 stream_id;
	u32 buffer_id;
};

static const struct drm_castkms_capture_format castkms_capture_formats[] = {
	{
		.format = DRM_FORMAT_XRGB8888,
		.modifier = DRM_FORMAT_MOD_LINEAR,
	},
};

static_assert(sizeof(struct drm_castkms_capture_format) == 16);
static_assert(CASTKMS_MIN_WIDTH == DRM_CASTKMS_CAPTURE_MIN_WIDTH);
static_assert(CASTKMS_MIN_HEIGHT == DRM_CASTKMS_CAPTURE_MIN_HEIGHT);
static_assert(CASTKMS_MAX_WIDTH == DRM_CASTKMS_CAPTURE_MAX_WIDTH);
static_assert(CASTKMS_MAX_HEIGHT == DRM_CASTKMS_CAPTURE_MAX_HEIGHT);
static_assert(CASTKMS_MAX_CURSOR_WIDTH ==
	      DRM_CASTKMS_CAPTURE_MAX_CURSOR_WIDTH);
static_assert(CASTKMS_MAX_CURSOR_HEIGHT ==
	      DRM_CASTKMS_CAPTURE_MAX_CURSOR_HEIGHT);
static_assert(CASTKMS_MAX_EDID_SIZE == DRM_CASTKMS_CAPTURE_MAX_EDID_SIZE);
static_assert(sizeof(struct drm_castkms_capture_query_caps) == 40);
static_assert(sizeof(struct drm_castkms_capture_start) == 24);
static_assert(sizeof(struct drm_castkms_capture_stop) == 16);
static_assert(sizeof(struct drm_castkms_capture_register_buffer) == 32);
static_assert(sizeof(struct drm_castkms_capture_unregister_buffer) == 16);
static_assert(sizeof(struct drm_castkms_capture_queue_buffer) == 48);
static_assert(sizeof(struct drm_event_castkms_capture_frame) == 112);
static_assert(offsetof(struct drm_event_castkms_capture_frame, reserved) == 108);
static_assert(sizeof(struct drm_castkms_capture_read_cursor_bitmap) == 40);

static void castkms_capture_uapi_stream_release(struct kref *ref)
{
	struct castkms_capture_uapi_stream *uapi_stream =
		container_of(ref, struct castkms_capture_uapi_stream, ref);

	WARN_ON(uapi_stream->published);
	WARN_ON(uapi_stream->resource.authority);
	WARN_ON(uapi_stream->stream);
	xa_destroy(&uapi_stream->buffers);
	castkms_capture_authority_put(uapi_stream->authority);
	kfree(uapi_stream);
}

static void
castkms_capture_uapi_stream_get(struct castkms_capture_uapi_stream *uapi_stream)
{
	kref_get(&uapi_stream->ref);
}

static void
castkms_capture_uapi_stream_put(struct castkms_capture_uapi_stream *uapi_stream)
{
	kref_put(&uapi_stream->ref, castkms_capture_uapi_stream_release);
}

static bool castkms_capture_uapi_stream_unpublish_locked(
	struct castkms_capture_uapi_stream *uapi_stream)
{
	struct castkms_file *file_state = uapi_stream->file_state;

	lockdep_assert_held(&file_state->capture_lock);
	if (!uapi_stream->published)
		return false;
	if (!WARN_ON(xa_load(&file_state->capture_streams,
			       uapi_stream->stream_id) != uapi_stream))
		xa_erase(&file_state->capture_streams, uapi_stream->stream_id);
	uapi_stream->published = false;
	return true;
}

static bool castkms_capture_uapi_stream_needs_cleanup(
	struct castkms_capture_authority_resource *resource,
	enum castkms_capture_authority_cleanup_reason reason, u64 generation)
{
	struct castkms_capture_uapi_stream *uapi_stream =
		container_of(resource, struct castkms_capture_uapi_stream,
			     resource);

	switch (reason) {
	case CASTKMS_CAPTURE_AUTHORITY_CLEANUP_MASTER_EPOCH:
		return castkms_capture_authority_generation_is_stale(
			castkms_capture_stream_authority_generation(
				uapi_stream->stream),
			generation);
	case CASTKMS_CAPTURE_AUTHORITY_CLEANUP_DISCONNECT:
		return true;
	default:
		return false;
	}
}

static void castkms_capture_uapi_stream_revoke(
	struct castkms_capture_authority_resource *resource, int status)
{
	struct castkms_capture_uapi_stream *uapi_stream =
		container_of(resource, struct castkms_capture_uapi_stream,
			     resource);
	struct castkms_capture_stream *stream = uapi_stream->stream;
	bool put_mapping;

	mutex_lock(&uapi_stream->file_state->capture_lock);
	put_mapping =
		castkms_capture_uapi_stream_unpublish_locked(uapi_stream);
	mutex_unlock(&uapi_stream->file_state->capture_lock);

	uapi_stream->stream = NULL;
	castkms_capture_stream_destroy(stream, status);
	if (put_mapping)
		castkms_capture_uapi_stream_put(uapi_stream);
	/* Drop the resource-lifetime reference last. */
	castkms_capture_uapi_stream_put(uapi_stream);
}

static const struct castkms_capture_authority_resource_ops
castkms_capture_uapi_stream_resource_ops = {
	.needs_cleanup = castkms_capture_uapi_stream_needs_cleanup,
	.revoke = castkms_capture_uapi_stream_revoke,
};

static void castkms_capture_uapi_stream_stop(
	struct castkms_capture_uapi_stream *uapi_stream, int status)
{
	struct castkms_capture_stream *stream;

	if (!castkms_capture_authority_unregister_resource(
		    uapi_stream->authority, &uapi_stream->resource))
		return;

	stream = uapi_stream->stream;
	uapi_stream->stream = NULL;
	castkms_capture_stream_destroy(stream, status);
	castkms_capture_uapi_stream_put(uapi_stream);
}

static bool castkms_capture_syncobjs_are_available(
	struct castkms_file *file_state,
	struct drm_syncobj *ready_syncobj, struct drm_syncobj *reuse_syncobj)
{
	struct castkms_capture_uapi_stream *uapi_stream;
	struct castkms_capture_buffer *buffer;
	struct dma_fence *ready_fence;
	unsigned long buffer_id;
	unsigned long stream_id;

	if (ready_syncobj == reuse_syncobj)
		return false;
	ready_fence = drm_syncobj_fence_get(ready_syncobj);
	if (ready_fence) {
		dma_fence_put(ready_fence);
		return false;
	}

	xa_for_each(&file_state->capture_streams, stream_id, uapi_stream) {
		xa_for_each(&uapi_stream->buffers, buffer_id, buffer) {
			if (castkms_capture_buffer_uses_syncobj(
				    buffer, ready_syncobj) ||
			    castkms_capture_buffer_uses_syncobj(
				    buffer, reuse_syncobj))
				return false;
		}
	}

	return true;
}

void castkms_capture_uapi_file_fini(struct drm_file *file_priv)
{
	struct castkms_file *file_state = file_priv->driver_priv;
	struct castkms_capture_uapi_stream *uapi_stream;
	unsigned long id = 0;
	bool put_mapping;

	for (;;) {
		mutex_lock(&file_state->capture_lock);
		uapi_stream = xa_find(&file_state->capture_streams, &id,
				      ULONG_MAX, XA_PRESENT);
		put_mapping = uapi_stream &&
			castkms_capture_uapi_stream_unpublish_locked(uapi_stream);
		mutex_unlock(&file_state->capture_lock);
		if (!uapi_stream)
			break;

		castkms_capture_uapi_stream_stop(uapi_stream, -ECANCELED);
		if (put_mapping)
			castkms_capture_uapi_stream_put(uapi_stream);
		id = 0;
	}
}

int castkms_capture_query_caps_ioctl(struct drm_device *dev, void *data,
				     struct drm_file *file_priv)
{
	struct drm_castkms_capture_query_caps *args = data;
	void __user *formats = u64_to_user_ptr(args->formats_ptr);
	u32 capacity = args->format_count;

	if (args->reserved)
		return -EINVAL;
	if (!drm_crtc_find(dev, file_priv, args->crtc_id))
		return -ENOENT;

	args->uapi_major = DRM_CASTKMS_CAPTURE_UAPI_MAJOR;
	args->uapi_minor = DRM_CASTKMS_CAPTURE_UAPI_MINOR;
	args->flags = DRM_CASTKMS_CAPTURE_CAP_IMPLICIT_SYNC |
		      DRM_CASTKMS_CAPTURE_CAP_GRANT_FD;
	if (drm_core_check_feature(dev, DRIVER_SYNCOBJ_TIMELINE))
		args->flags |= DRM_CASTKMS_CAPTURE_CAP_SYNCOBJ_TIMELINE;
	args->max_registered_buffers = CASTKMS_CAPTURE_MAX_BUFFERS;
	args->format_count = ARRAY_SIZE(castkms_capture_formats);

	if (capacity >= ARRAY_SIZE(castkms_capture_formats) &&
	    copy_to_user(formats, castkms_capture_formats,
			 sizeof(castkms_capture_formats)))
		return -EFAULT;

	return 0;
}

int castkms_capture_start_ioctl(struct drm_device *dev, void *data,
				struct drm_file *file_priv)
{
	struct drm_castkms_capture_start *args = data;
	struct castkms_file *file_state = file_priv->driver_priv;
	struct castkms_capture_uapi_stream *uapi_stream;
	struct castkms_capture_authority *authority;
	struct castkms_capture_stream *stream;
	struct castkms_output *output;
	struct drm_crtc *crtc;
	u32 rights = CASTKMS_CAPTURE_AUTHORITY_CAPTURE_PIXELS;
	u32 stream_id;
	bool exclude_cursor;
	bool put_mapping = false;
	int ret;

	if (!(args->flags & DRM_CASTKMS_CAPTURE_START_EXCLUSIVE) ||
	    (args->flags & ~(DRM_CASTKMS_CAPTURE_START_EXCLUSIVE |
			     DRM_CASTKMS_CAPTURE_START_EXCLUDE_CURSOR)) ||
	    args->reserved)
		return -EINVAL;
	args->stream_id = 0;
	args->mode_generation = 0;

	crtc = drm_crtc_find(dev, file_priv, args->crtc_id);
	if (!crtc)
		return -ENOENT;
	output = drm_crtc_to_castkms_output(crtc);
	exclude_cursor = args->flags &
			 DRM_CASTKMS_CAPTURE_START_EXCLUDE_CURSOR;
	if (!exclude_cursor)
		rights |= CASTKMS_CAPTURE_AUTHORITY_READ_CURSOR;

	ret = castkms_grant_begin_crtc(file_priv, crtc, rights, &authority);
	if (ret)
		return ret;

	stream = castkms_capture_stream_create(output, authority, exclude_cursor,
					       &args->mode_generation);
	if (IS_ERR(stream)) {
		ret = PTR_ERR(stream);
		goto out_end_grant;
	}
	uapi_stream = kzalloc_obj(*uapi_stream);
	if (!uapi_stream) {
		ret = -ENOMEM;
		goto out_destroy_stream;
	}
	kref_init(&uapi_stream->ref);
	uapi_stream->authority = authority;
	castkms_capture_authority_get(authority);
	uapi_stream->file_state = file_state;
	uapi_stream->stream = stream;
	xa_init_flags(&uapi_stream->buffers, XA_FLAGS_ALLOC);
	ret = castkms_capture_authority_register_resource(
		authority, &uapi_stream->resource,
		&castkms_capture_uapi_stream_resource_ops);
	if (ret)
		goto out_destroy_unregistered_uapi_stream;

	ret = castkms_capture_stream_status(stream);
	if (ret)
		goto out_stop_uapi_stream;

	mutex_lock(&file_state->capture_lock);
	ret = xa_alloc(&file_state->capture_streams, &stream_id, uapi_stream,
		       XA_LIMIT(1, INT_MAX), GFP_KERNEL);
	if (ret)
		goto out_unlock;
	uapi_stream->stream_id = stream_id;
	uapi_stream->published = true;
	castkms_capture_uapi_stream_get(uapi_stream);
	ret = castkms_capture_stream_attach(stream);
	if (!ret)
		ret = castkms_capture_stream_status(stream);
	if (!ret)
		ret = castkms_capture_stream_validate_mode(
			stream, args->mode_generation);
	if (!ret) {
		ret = castkms_capture_stream_status(stream);
		if (ret == -EACCES)
			ret = -ESTALE;
	}
	if (ret) {
		put_mapping =
			castkms_capture_uapi_stream_unpublish_locked(uapi_stream);
		goto out_unlock;
	}
	mutex_unlock(&file_state->capture_lock);

	args->stream_id = stream_id;
	castkms_grant_end(authority);
	return 0;

out_unlock:
	mutex_unlock(&file_state->capture_lock);
	if (put_mapping)
		castkms_capture_uapi_stream_put(uapi_stream);
out_stop_uapi_stream:
	castkms_capture_uapi_stream_stop(uapi_stream, -ECANCELED);
	goto out_clear_generation;
out_destroy_unregistered_uapi_stream:
	uapi_stream->stream = NULL;
	castkms_capture_stream_destroy(stream, -ECANCELED);
	castkms_capture_uapi_stream_put(uapi_stream);
	goto out_clear_generation;
out_destroy_stream:
	castkms_capture_stream_destroy(stream, -ECANCELED);
out_clear_generation:
	args->mode_generation = 0;
out_end_grant:
	castkms_grant_end(authority);
	return ret;
}
int castkms_capture_stop_ioctl(struct drm_device *dev, void *data,
			       struct drm_file *file_priv)
{
	struct drm_castkms_capture_stop *args = data;
	struct castkms_file *file_state = file_priv->driver_priv;
	struct castkms_capture_uapi_stream *uapi_stream;
	bool put_mapping;

	(void)dev;
	if (args->flags || args->reserved)
		return -EINVAL;

	mutex_lock(&file_state->capture_lock);
	uapi_stream = xa_load(&file_state->capture_streams, args->stream_id);
	if (!uapi_stream) {
		mutex_unlock(&file_state->capture_lock);
		return -ENOENT;
	}
	put_mapping =
		castkms_capture_uapi_stream_unpublish_locked(uapi_stream);
	mutex_unlock(&file_state->capture_lock);

	castkms_capture_uapi_stream_stop(uapi_stream, -ECANCELED);
	if (put_mapping)
		castkms_capture_uapi_stream_put(uapi_stream);

	return 0;
}

static int castkms_capture_uapi_validate_stream(
	struct drm_device *dev, struct drm_file *file_priv,
	struct castkms_capture_uapi_stream *uapi_stream,
	struct castkms_capture_authority *authority)
{
	struct castkms_output *output;
	int ret;

	if (!castkms_capture_stream_has_authority(uapi_stream->stream,
						     authority))
		return -EACCES;
	ret = castkms_capture_stream_status(uapi_stream->stream);
	if (ret)
		return ret;

	output = castkms_capture_stream_output(uapi_stream->stream);
	if (drm_crtc_find(dev, file_priv, output->crtc.base.id) !=
	    &output->crtc)
		return -ENOENT;

	return 0;
}

int castkms_capture_register_buffer_ioctl(struct drm_device *dev, void *data,
					  struct drm_file *file_priv)
{
	struct drm_castkms_capture_register_buffer *args = data;
	struct castkms_file *file_state = file_priv->driver_priv;
	struct castkms_capture_uapi_stream *uapi_stream;
	struct castkms_capture_authority *authority;
	struct castkms_capture_buffer *buffer;
	struct drm_syncobj *ready_syncobj = NULL;
	struct drm_syncobj *reuse_syncobj = NULL;
	struct drm_framebuffer *fb;
	enum castkms_capture_sync_mode sync_mode;
	u32 buffer_id;
	int ret;

	args->buffer_id = 0;
	if (args->flags != DRM_CASTKMS_CAPTURE_BUFFER_IMPLICIT_SYNC &&
	    args->flags != DRM_CASTKMS_CAPTURE_BUFFER_EXPLICIT_SYNC)
		return -EINVAL;
	if (args->flags == DRM_CASTKMS_CAPTURE_BUFFER_IMPLICIT_SYNC &&
	    (args->ready_syncobj_handle || args->reuse_syncobj_handle))
		return -EINVAL;
	if (args->flags == DRM_CASTKMS_CAPTURE_BUFFER_EXPLICIT_SYNC &&
	    (!args->ready_syncobj_handle || !args->reuse_syncobj_handle))
		return -EINVAL;

	ret = castkms_grant_begin(file_priv, NULL,
				  CASTKMS_CAPTURE_AUTHORITY_CAPTURE_PIXELS,
				  &authority);
	if (ret)
		return ret;

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

	fb = drm_framebuffer_lookup(dev, file_priv, args->fb_id);
	if (!fb) {
		ret = -ENOENT;
		goto out_unlock;
	}

	if (args->flags == DRM_CASTKMS_CAPTURE_BUFFER_EXPLICIT_SYNC) {
		ready_syncobj = drm_syncobj_find(file_priv,
						 args->ready_syncobj_handle);
		if (!ready_syncobj) {
			ret = -ENOENT;
			goto out_put_fb;
		}
		reuse_syncobj = drm_syncobj_find(file_priv,
						 args->reuse_syncobj_handle);
		if (!reuse_syncobj) {
			ret = -ENOENT;
			goto out_put_syncobj;
		}
		if (!castkms_capture_syncobjs_are_available(file_state,
							    ready_syncobj,
							    reuse_syncobj)) {
			ret = -EINVAL;
			goto out_put_syncobj;
		}
		sync_mode = CASTKMS_CAPTURE_SYNC_EXPLICIT;
	} else {
		sync_mode = CASTKMS_CAPTURE_SYNC_IMPLICIT;
	}

	buffer = castkms_capture_buffer_create(uapi_stream->stream, fb,
					       ready_syncobj, reuse_syncobj,
					       sync_mode,
					       args->mode_generation);
	if (IS_ERR(buffer)) {
		ret = PTR_ERR(buffer);
		goto out_put_syncobj;
	}

	ret = xa_alloc(&uapi_stream->buffers, &buffer_id, buffer,
		       XA_LIMIT(1, CASTKMS_CAPTURE_MAX_BUFFERS), GFP_KERNEL);
	if (ret)
		goto out_remove_buffer;
	args->buffer_id = buffer_id;
	ret = 0;
	goto out_put_syncobj;

out_remove_buffer:
	castkms_capture_buffer_remove(uapi_stream->stream, buffer);
out_put_syncobj:
	if (reuse_syncobj)
		drm_syncobj_put(reuse_syncobj);
	if (ready_syncobj)
		drm_syncobj_put(ready_syncobj);
out_put_fb:
	drm_framebuffer_put(fb);
out_unlock:
	mutex_unlock(&file_state->capture_lock);
	castkms_grant_end(authority);
	return ret;
}

int castkms_capture_unregister_buffer_ioctl(struct drm_device *dev, void *data,
					    struct drm_file *file_priv)
{
	struct drm_castkms_capture_unregister_buffer *args = data;
	struct castkms_file *file_state = file_priv->driver_priv;
	struct castkms_capture_uapi_stream *uapi_stream;
	struct castkms_capture_buffer *buffer;
	int ret;

	(void)dev;
	if (args->flags || args->reserved)
		return -EINVAL;

	mutex_lock(&file_state->capture_lock);
	uapi_stream = xa_load(&file_state->capture_streams, args->stream_id);
	if (!uapi_stream) {
		ret = -ENOENT;
		goto out_unlock;
	}

	buffer = xa_load(&uapi_stream->buffers, args->buffer_id);
	if (!buffer) {
		ret = -ENOENT;
		goto out_unlock;
	}

	ret = castkms_capture_buffer_remove(uapi_stream->stream, buffer);
	if (!ret)
		xa_erase(&uapi_stream->buffers, args->buffer_id);

out_unlock:
	mutex_unlock(&file_state->capture_lock);
	return ret;
}

static void castkms_capture_uapi_request_complete(
	struct castkms_capture_request *request,
	const struct castkms_capture_result *result)
{
	struct castkms_capture_uapi_request *uapi_request = container_of(
		request, struct castkms_capture_uapi_request, request);
	struct drm_event_castkms_capture_frame *event = &uapi_request->event;

	if (result->cancelled) {
		drm_event_cancel_free(uapi_request->dev, &uapi_request->pending);
		return;
	}

	event->user_data = uapi_request->user_data;
	event->sequence = result->sequence;
	event->timestamp_ns = ktime_to_ns(result->timestamp);
	event->mode_generation = result->mode_generation;
	event->stream_id = uapi_request->stream_id;
	event->buffer_id = uapi_request->buffer_id;
	event->status = result->status;
	if (result->mode_changed)
		event->flags |= DRM_CASTKMS_CAPTURE_FRAME_MODE_CHANGED;
	if (result->full_damage)
		event->flags |= DRM_CASTKMS_CAPTURE_FRAME_FULL_DAMAGE;
	event->dropped_frames = result->dropped_frames;
	event->damage_x = result->damage.x1;
	event->damage_y = result->damage.y1;
	event->damage_width = drm_rect_width(&result->damage);
	event->damage_height = drm_rect_height(&result->damage);
	event->cursor_serial = result->cursor_serial;
	if (result->cursor_visible)
		event->cursor_flags |= DRM_CASTKMS_CURSOR_VISIBLE;
	if (result->cursor_image_changed)
		event->cursor_flags |= DRM_CASTKMS_CURSOR_IMAGE_CHANGED;
	event->cursor_x = result->cursor_x;
	event->cursor_y = result->cursor_y;
	event->cursor_hotspot_x = result->cursor_hotspot_x;
	event->cursor_hotspot_y = result->cursor_hotspot_y;
	event->cursor_width = result->cursor_width;
	event->cursor_height = result->cursor_height;

	/* drm_send_event() takes ownership of the adapter allocation. */
	drm_send_event(uapi_request->dev, &uapi_request->pending);
}

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

	ret = castkms_grant_begin(file_priv, NULL,
				  CASTKMS_CAPTURE_AUTHORITY_CAPTURE_PIXELS,
				  &authority);
	if (ret)
		return ret;

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

	uapi_request = kzalloc_obj(*uapi_request);
	if (!uapi_request) {
		ret = -ENOMEM;
		goto out_unlock;
	}
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
		goto out_unlock;
	}

	ret = castkms_capture_buffer_submit(buffer, &uapi_request->request);
	if (ret)
		drm_event_cancel_free(dev, &uapi_request->pending);

out_unlock:
	mutex_unlock(&file_state->capture_lock);
	castkms_grant_end(authority);
	return ret;
}

int castkms_capture_read_cursor_bitmap_ioctl(struct drm_device *dev, void *data,
					     struct drm_file *file_priv)
{
	struct drm_castkms_capture_read_cursor_bitmap *args = data;
	struct castkms_file *file_state = file_priv->driver_priv;
	struct castkms_capture_uapi_stream *uapi_stream;
	struct castkms_capture_buffer *buffer;
	struct castkms_capture_cursor_data cursor;
	struct castkms_capture_authority *authority;
	int ret;

	(void)dev;
	if (args->reserved)
		return -EINVAL;

	ret = castkms_grant_begin(file_priv, NULL,
				  CASTKMS_CAPTURE_AUTHORITY_CAPTURE_PIXELS |
				  CASTKMS_CAPTURE_AUTHORITY_READ_CURSOR,
				  &authority);
	if (ret)
		return ret;

	mutex_lock(&file_state->capture_lock);
	uapi_stream = xa_load(&file_state->capture_streams, args->stream_id);
	if (!uapi_stream) {
		ret = -ENOENT;
		goto out_unlock;
	}
	if (!castkms_capture_stream_has_authority(uapi_stream->stream,
						     authority)) {
		ret = -EACCES;
		goto out_unlock;
	}
	ret = castkms_capture_stream_status(uapi_stream->stream);
	if (ret)
		goto out_unlock;

	buffer = xa_load(&uapi_stream->buffers, args->buffer_id);
	if (!buffer) {
		ret = -ENOENT;
		goto out_unlock;
	}
	ret = castkms_capture_buffer_get_cursor_data(buffer, &cursor);
	if (ret)
		goto out_unlock;

	if (!cursor.bitmap || !cursor.size) {
		args->format = 0;
		args->width = 0;
		args->height = 0;
		args->stride = 0;
		args->bitmap_size = 0;
		ret = 0;
		goto out_unlock;
	}

	args->format = DRM_FORMAT_ARGB8888;
	args->width = cursor.width;
	args->height = cursor.height;
	args->stride = cursor.stride;
	if (!args->bitmap_size) {
		args->bitmap_size = cursor.size;
		ret = 0;
		goto out_unlock;
	}
	if (args->bitmap_size < cursor.size) {
		args->bitmap_size = cursor.size;
		ret = -ENOSPC;
		goto out_unlock;
	}
	if (copy_to_user(u64_to_user_ptr(args->bitmap_ptr), cursor.bitmap,
			 cursor.size)) {
		ret = -EFAULT;
		goto out_unlock;
	}

	args->bitmap_size = cursor.size;
	ret = 0;

out_unlock:
	mutex_unlock(&file_state->capture_lock);
	castkms_grant_end(authority);
	return ret;
}
