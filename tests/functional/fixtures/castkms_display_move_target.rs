// SPDX-License-Identifier: GPL-2.0-only

//! One development output and its accepted display descriptions.

use super::{
    output::SceneUpdate,
    provenance::{
        PreviousOwner,
        Provenance,
        Selection, //
    },
    scene,
    Driver, //
};
use core::marker::PhantomData;
use crtc::{
    RawCrtc,
    RawCrtcState, //
};
use kernel::{
    device,
    drm::{
        fourcc,
        kms::*,
        Device, //
    },
    prelude::*,
    sync::Arc, //
};
use plane::RawPlaneState;

#[pin_data]
pub(super) struct Plane {}
#[pin_data]
pub(super) struct Crtc {}
#[pin_data]
pub(super) struct Encoder {}
#[pin_data]
pub(super) struct Connector {}

pub(super) struct State;

pub(super) struct PlaneState {
    geometry: Option<scene::Geometry>,
    content: Option<scene::ContentSerial>,
    selection: Selection,
    owner: Option<kernel::drm::auth::MasterRef<Driver>>,
    producer: Option<Arc<framebuffer::dependencies::Dependencies>>,
}

impl plane::DriverPlaneState for PlaneState {
    type Plane = Plane;
    fn new(_: &plane::Plane<Plane>) -> Result<Self> {
        Ok(Self {
            geometry: None,
            content: None,
            selection: Selection::RetainedFramebuffer,
            owner: None,
            producer: None,
        })
    }
    fn duplicate(&self) -> Result<Self> {
        Ok(Self {
            geometry: None,
            content: self.content,
            selection: Selection::RetainedFramebuffer,
            owner: self.owner.clone(),
            producer: None,
        })
    }
}

impl crtc::DriverCrtcState for State {
    type Crtc = Crtc;
    fn new(_: &crtc::Crtc<Crtc>) -> Result<Self> {
        Ok(Self)
    }
    fn duplicate(&self) -> Result<Self> {
        Ok(Self)
    }
}

impl connector::DriverConnectorState for State {
    type Connector = Connector;
    fn new(_: &connector::Connector<Connector>) -> Result<Self> {
        Ok(Self)
    }
    fn duplicate(&self) -> Result<Self> {
        Ok(Self)
    }
}

fn check_geometry(
    transaction: &atomic::AtomicStateComposer<Driver>,
    state: &mut plane::PlaneStateMutator<'_, plane::PlaneState<PlaneState>>,
) -> Result {
    state.geometry = None;
    if let Some(crtc) = state.crtc() {
        let crtc_state = transaction.add_crtc_state(crtc)?;
        state.atomic_helper_check(&crtc_state, false, false)?;
        if crtc_state.active() && state.visible() {
            state.geometry = Some(scene::Geometry {
                source: [
                    state.source_x_16_16(),
                    state.source_y_16_16(),
                    state.source_width_16_16(),
                    state.source_height_16_16(),
                ],
                destination: [state.crtc_w(), state.crtc_h()],
                output: [
                    u32::from(crtc_state.mode().hdisplay()),
                    u32::from(crtc_state.mode().vdisplay()),
                ],
            });
        }
    }
    Ok(())
}

fn resolve_owner(
    old: &plane::PlaneState<PlaneState>,
    state: &plane::PlaneStateMutator<'_, plane::PlaneState<PlaneState>>,
    current: Option<&kernel::drm::auth::MasterRef<Driver>>,
) -> Option<kernel::drm::auth::MasterRef<Driver>> {
    let framebuffer = state.framebuffer()?;
    let previous = if old
        .framebuffer()
        .is_some_and(|old| core::ptr::eq(old, framebuffer))
    {
        PreviousOwner::SameFramebuffer(old.owner.as_ref())
    } else {
        PreviousOwner::DifferentFramebuffer
    };
    Provenance::for_update(framebuffer.data(), previous, current, state.selection).cloned()
}

#[vtable]
impl plane::DriverPlane for Plane {
    type Args = ();
    type Driver = Driver;
    type State = PlaneState;

    fn new(_: &Device<Driver>, _: ()) -> impl PinInit<Self, Error> {
        try_pin_init!(Self {})
    }

    fn atomic_check(check: plane::PlaneAtomicCheck<'_, Self>) -> Result {
        let (transaction, old, mut state) = check.take_all();
        check_geometry(transaction, &mut state)?;
        state.content = scene::ContentSerial::for_update(old.content, state.geometry.is_some())?;
        state.selection = Selection::for_update(
            transaction.plane_input(state.plane())?,
            old.framebuffer(),
            state.framebuffer(),
        );
        let current = transaction.drm_dev().authority.snapshot();
        state.owner = resolve_owner(old, &state, current.as_ref());
        Ok(())
    }

    fn prepare_framebuffer(
        mut state: plane::PlaneStateMutator<'_, plane::PlaneState<PlaneState>>,
    ) -> Result {
        state.producer = None;
        if let Some(framebuffer) = state.framebuffer() {
            let dependencies = framebuffer::dependencies::Dependencies::acquire(
                framebuffer,
                state.producer_fence(),
            )?;
            let completion = dependencies.completion()?;
            let dependencies = Arc::new(dependencies, GFP_KERNEL)?;
            state.set_producer_fence(completion);
            state.producer = Some(dependencies);
        }
        Ok(())
    }
}

impl PlaneState {
    fn scene(state: &plane::PlaneState<Self>) -> Option<scene::Scene> {
        state
            .geometry
            .zip(state.content)
            .and_then(|(geometry, content)| {
                state.framebuffer().map(|framebuffer| {
                    scene::Scene::new(
                        framebuffer.to_owned_ref(),
                        geometry,
                        content,
                        state.owner.clone(),
                        state.producer.clone(),
                    )
                })
            })
    }
}

#[vtable]
impl crtc::DriverCrtc for Crtc {
    type Args = ();
    type Driver = Driver;
    type State = State;
    type VblankImpl = PhantomData<Self>;

    fn new(_: &Device<Driver>, _: &()) -> impl PinInit<Self, Error> {
        try_pin_init!(Self {})
    }

    fn atomic_flush(commit: crtc::CrtcAtomicCommit<'_, Self>) {
        let Some(source) = commit.preparation_source() else {
            commit.take_state().drm_dev().output.close();
            return;
        };
        let primary = commit.crtc().primary_plane();
        let (transaction, _, state) = commit.take_all();
        let update = if !state.active() {
            SceneUpdate::Replace(None)
        } else {
            match transaction.get_new_plane_state(primary) {
                Some(plane) => SceneUpdate::Replace(PlaneState::scene(plane)),
                None => SceneUpdate::Retain,
            }
        };
        transaction.drm_dev().output.publish(source, update);
    }
}

#[vtable]
impl encoder::DriverEncoder for Encoder {
    type Args = ();
    type Driver = Driver;

    fn new(_: &Device<Driver>, _: ()) -> impl PinInit<Self, Error> {
        try_pin_init!(Self {})
    }
}

#[vtable]
impl connector::DriverConnector for Connector {
    type Args = ();
    type Driver = Driver;
    type State = State;

    fn new(_: &Device<Driver>, _: ()) -> impl PinInit<Self, Error> {
        try_pin_init!(Self {})
    }

    fn get_modes<'a>(
        connector: connector::ConnectorGuard<'a, Self>,
        _: &ModeConfigGuard<'a, Driver>,
    ) -> i32 {
        let count = connector.add_modes_noedid((1920, 1080));
        connector.set_preferred_mode((1920, 1080));
        count
    }
}

#[vtable]
impl KmsDriver for Driver {
    type FramebufferData = super::provenance::Provenance;
    fn framebuffer_data(
        _: &Device<Self>,
        file: Option<&kernel::drm::file::File<Self::File>>,
    ) -> Result<Self::FramebufferData> {
        Ok(super::provenance::Provenance::from_snapshot(
            file.and_then(|file| file.master_snapshot()),
        ))
    }
    type Connector = Connector;
    type Plane = Plane;
    type Crtc = Crtc;
    type Encoder = Encoder;

    fn mode_config_info(
        _: &device::Device,
        _: &<Self as kernel::drm::Driver>::Data,
    ) -> Result<ModeConfigInfo> {
        Ok(ModeConfigInfo {
            min_resolution: (1, 1),
            max_resolution: (1920, 1080),
            max_cursor: (0, 0),
            preferred_depth: 24,
            preferred_fourcc: Some(fourcc::XRGB8888),
            enable_default_client: false,
        })
    }

    fn create_objects(dev: &UnregisteredKmsDevice<'_, Self>) -> Result {
        dev.enable_preparation(8)?;
        let plane = plane::UnregisteredPlane::<Plane>::new(
            dev,
            0,
            &[fourcc::XRGB8888],
            Some(&[fourcc::FORMAT_MOD_LINEAR]),
            plane::Type::Primary,
            None,
            (),
        )?;
        let crtc = crtc::UnregisteredCrtc::<Crtc>::new(
            dev,
            plane,
            None::<&plane::UnregisteredPlane<Plane>>,
            None,
            (),
        )?;
        let encoder = encoder::UnregisteredEncoder::<Encoder>::new(
            dev,
            encoder::Type::Virtual,
            crtc.mask(),
            0,
            None,
            (),
        )?;
        let connector =
            connector::UnregisteredConnector::<Connector>::new(dev, connector::Type::Virtual, ())?;
        connector.attach_encoder(encoder)
    }

    fn atomic_commit_tail<'a>(
        mut tail: atomic::AtomicCommitTail<'a, Self>,
        modesets: atomic::ModesetsReadyToken<'a, Self>,
        planes: atomic::PlaneUpdatesReadyToken<'a, Self>,
    ) -> atomic::CommittedAtomicState<'a, Self> {
        let disabled = tail.commit_modeset_disables(modesets);
        let planes = tail.commit_planes(planes, atomic::PlaneCommitFlags::default());
        let enabled = tail.commit_modeset_enables(disabled);
        // No pixel reader survives the commit. Complete events immediately using DRM's
        // no-vblank path, not a claim that a receiver presented the frame.
        tail.fake_vblank();
        tail.commit_hw_done(enabled, planes)
    }
}
