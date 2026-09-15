/// Validated crop, destination dimensions and complete output dimensions.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SourceGeometry {
    source: SourceRect,
    destination: Extent,
    output: Extent,
}

impl SourceGeometry {
    /// Describe an integral crop placed at the top-left of a complete output.
    ///
    /// The destination may scale the crop but must fit inside the output.
    /// Geometry grants no source access and does not establish native support
    /// for the image's format, modifier or sampling operation.
    pub fn new(source: SourceRect, destination: Extent, output: Extent) -> io::Result<Self> {
        if destination.width() > output.width() || destination.height() > output.height() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "source destination exceeds the output dimensions",
            ));
        }
        Ok(Self {
            source,
            destination,
            output,
        })
    }

    pub fn source(self) -> SourceRect {
        self.source
    }

    pub fn destination(self) -> Extent {
        self.destination
    }

    pub fn output(self) -> Extent {
        self.output
    }
}

    let source = SourceRect::from_fixed_16_16(extent, result.source)
        .map_err(|_| invalid_data("CastKMS returned invalid source coordinates"))?;
    let destination = Extent::new(result.destination[0], result.destination[1])
        .map_err(|_| invalid_data("CastKMS returned empty destination dimensions"))?;
    let output = Extent::new(result.output[0], result.output[1])
        .map_err(|_| invalid_data("CastKMS returned empty output dimensions"))?;
    let geometry = SourceGeometry::new(source, destination, output)
        .map_err(|_| invalid_data("CastKMS returned destination dimensions outside the output"))?;

    Ok(SourceDescription {
        id,
        content_serial,
        image: SourceImage {
            format: result.format,
            modifier: if result.modifier == DRM_FORMAT_MOD_INVALID {
                FormatModifier::Unspecified
            } else {
                FormatModifier::Explicit(result.modifier)
            },
            extent,
            planes,
            plane_count,
        },
        geometry,
        producer: returned.producer,
    })
}

mod tests {
    #[test]
    fn source_validation_adopts_descriptors_and_geometry() {
        let mut result = source_result();
        result.producer_fd = descriptor();
        let source = validate_source(result).unwrap();
        assert_eq!(source.id.get(), 13);
        assert_eq!(source.content_serial.get(), 14);
        assert_eq!(source.image.format(), DRM_FORMAT_XRGB8888);
        assert_eq!(source.image.modifier(), FormatModifier::Unspecified);
        assert_eq!(source.image.extent().width(), 1920);
        assert_eq!(source.image.planes().len(), 1);
        assert_eq!(source.image.planes().next().unwrap().pitch().get(), 7680);
        assert_eq!(source.geometry.source().extent().width(), 1920);
        assert_eq!(source.geometry.destination().height(), 1080);
        assert!(source.producer.is_some());
    }

    #[test]
    fn geometry_preserves_cropping_scaling_and_output_padding() {
        let image = Extent::new(1920, 1080).unwrap();
        let crop = SourceRect::new(image, [20, 30], Extent::new(640, 360).unwrap()).unwrap();
        let destination = Extent::new(1280, 720).unwrap();
        let geometry = SourceGeometry::new(crop, destination, image).unwrap();
        assert_eq!(geometry.source(), crop);
        assert_eq!(geometry.destination(), destination);
        assert_eq!(geometry.output(), image);
        for destination in [
            Extent::new(1921, 1080).unwrap(),
            Extent::new(1920, 1081).unwrap(),
        ] {
            assert_eq!(
                SourceGeometry::new(crop, destination, image)
                    .unwrap_err()
                    .kind(),
                io::ErrorKind::InvalidInput
            );
        }
    }

    #[test]
    fn source_result_decodes_cropped_and_scaled_geometry() {
        let mut result = source_result();
        result.source = [20 << 16, 30 << 16, 640 << 16, 360 << 16];
        result.destination = [1280, 720];
        let source = validate_source(result).unwrap();
        assert_eq!(source.geometry.source().origin(), [20, 30]);
        assert_eq!(
            source.geometry.source().extent(),
            Extent::new(640, 360).unwrap()
        );
        assert_eq!(
            source.geometry.destination(),
            Extent::new(1280, 720).unwrap()
        );
        assert_eq!(source.geometry.output(), Extent::new(1920, 1080).unwrap());

        let mut result = source_result();
        result.destination[0] = result.output[0] + 1;
        assert_eq!(
            validate_source(result).err().unwrap().kind(),
            io::ErrorKind::InvalidData
        );
    }

    #[test]
    fn source_validation_closes_descriptors_from_malformed_results() {
        let mut result = source_result();
        let first = result.planes[0].dma_buf_fd;
        let second = descriptor();
        result.planes[1].dma_buf_fd = second;
        assert!(validate_source(result).is_err());
        assert_eq!(
            fcntl(first, FcntlArg::F_GETFD),
            Err(nix::errno::Errno::EBADF)
        );
        assert_eq!(
            fcntl(second, FcntlArg::F_GETFD),
            Err(nix::errno::Errno::EBADF)
        );

        let mut result = source_result();
        let duplicate = result.planes[0].dma_buf_fd;
        result.producer_fd = duplicate;
        assert!(validate_source(result).is_err());
        assert_eq!(
            fcntl(duplicate, FcntlArg::F_GETFD),
            Err(nix::errno::Errno::EBADF)
        );
    }

}
