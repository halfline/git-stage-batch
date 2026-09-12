// SPDX-License-Identifier: GPL-2.0-only

//! Checked storage and sampling geometry, without pixel-read authority.

#![expect(dead_code)]

use crate::{
    scene::Geometry,
    Driver, //
};
use kernel::{
    drm::{
        fourcc,
        gem::BaseObject,
        kms::framebuffer::{
            Framebuffer as KmsFramebuffer,
            FramebufferRef, //
        }, //
    },
    prelude::*, //
};

/// A retained native framebuffer satisfying the host compositor's storage limits.
///
/// Construction checks storage and sampling only. Scene color policy, capture authority and
/// source-read accounting remain separate. Mapping storage does not authorize pixel access.
pub(crate) struct Framebuffer {
    image: FramebufferRef<Driver>,
}

impl Framebuffer {
    pub(crate) fn new(image: &KmsFramebuffer<Driver>, geometry: Geometry) -> Result<Self> {
        let width = image.width();
        let height = image.height();
        if width == 0 || height == 0 || width > 1920 || height > 1080 {
            return Err(EINVAL);
        }
        if image.format() != fourcc::XRGB8888
            || image.plane_count() != 1
            || image.is_interlaced()
            || image
                .modifier()
                .is_some_and(|modifier| modifier != fourcc::FORMAT_MOD_LINEAR)
            || geometry.source != [0, 0, width << 16, height << 16]
            || geometry.destination != [width, height]
            || geometry.output != [width, height]
        {
            return Err(EINVAL);
        }
        let object = image.object_at(0)?;
        if object.imported_dma_buf().is_some() {
            return Err(EOPNOTSUPP);
        }
        let pitch = image.pitch(0)? as usize;
        let offset = image.offset(0)? as usize;
        if pitch % 4 != 0 || offset % 4 != 0 || pitch < width as usize * 4 {
            return Err(EINVAL);
        }
        let size = object.size();
        if size > 16 * 1024 * 1024 {
            return Err(E2BIG);
        }
        let end = pitch
            .checked_mul(height as usize)
            .and_then(|bytes| bytes.checked_add(offset))
            .ok_or(EOVERFLOW)?;
        if end > size {
            return Err(EINVAL);
        }
        Ok(Self {
            image: image.to_owned_ref(),
        })
    }

    pub(crate) fn dimensions(&self) -> (u32, u32) {
        (self.image.width(), self.image.height())
    }

}
