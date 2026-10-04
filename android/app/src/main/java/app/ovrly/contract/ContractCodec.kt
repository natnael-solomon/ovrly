package app.ovrly.contract

/*
 * Production parsers and encoders for the non-voice schemas of `packages/contracts`, one
 * object per resource area, parallel to [VoiceActionCodec]. These are the entry points #18
 * (AN-03) and #33 reuse; do not add a second parser or DTO set. Request bodies parse with
 * [ContractJson.strict] (an unknown key fails), read models with [ContractJson.tolerant]
 * (unknown keys are additive and ignored). Every failure is a [ContractParseException] that
 * names the first violation; nothing is replaced by a default.
 */

/** `POST /v1/investigations` and the investigation read model, including nested reports. */
internal object InvestigationCodec {
    fun parseInvestigation(payload: String): Investigation = ContractJson.parse("investigation") {
        ContractJson.tolerant.decodeFromString(Investigation.serializer(), payload)
    }

    /** A report version on its own, as `GET .../reports/{version}` will return it (#33). */
    fun parseReportVersion(payload: String): ReportVersion = ContractJson.parse("report version") {
        ContractJson.tolerant.decodeFromString(ReportVersion.serializer(), payload)
    }

    fun parseCreateRequest(payload: String): InvestigationCreateRequest =
        ContractJson.parse("investigation-create request") {
            ContractJson.strict.decodeFromString(InvestigationCreateRequest.serializer(), payload)
        }

    fun encodeCreateRequest(request: InvestigationCreateRequest): String = ContractJson.encode {
        ContractJson.strict.encodeToString(InvestigationCreateRequest.serializer(), request)
    }

    /** Encodes a read model, for fakes and tests; a model carrying UNKNOWN cannot be encoded. */
    fun encodeInvestigation(investigation: Investigation): String = ContractJson.encode {
        ContractJson.tolerant.encodeToString(Investigation.serializer(), investigation)
    }

    /** Encodes a report version, for fakes and tests. */
    fun encodeReportVersion(report: ReportVersion): String = ContractJson.encode {
        ContractJson.tolerant.encodeToString(ReportVersion.serializer(), report)
    }
}

/** `POST /v1/uploads`, `POST /v1/uploads/{id}/complete` and the upload read model. */
internal object UploadCodec {
    fun parseUpload(payload: String): Upload = ContractJson.parse("upload") {
        ContractJson.tolerant.decodeFromString(Upload.serializer(), payload)
    }

    fun parseDeclareRequest(payload: String): UploadDeclareRequest =
        ContractJson.parse("upload-declare request") {
            ContractJson.strict.decodeFromString(UploadDeclareRequest.serializer(), payload)
        }

    fun parseCompleteRequest(payload: String): UploadCompleteRequest =
        ContractJson.parse("upload-complete request") {
            ContractJson.strict.decodeFromString(UploadCompleteRequest.serializer(), payload)
        }

    fun encodeDeclareRequest(request: UploadDeclareRequest): String = ContractJson.encode {
        ContractJson.strict.encodeToString(UploadDeclareRequest.serializer(), request)
    }

    fun encodeCompleteRequest(request: UploadCompleteRequest): String = ContractJson.encode {
        ContractJson.strict.encodeToString(UploadCompleteRequest.serializer(), request)
    }

    /** Encodes a read model, for fakes and tests; a model carrying UNKNOWN cannot be encoded. */
    fun encodeUpload(upload: Upload): String = ContractJson.encode {
        ContractJson.tolerant.encodeToString(Upload.serializer(), upload)
    }
}

/** Live capture sessions and chunks (schemas published ahead of their endpoints). */
internal object CaptureCodec {
    fun parseSession(payload: String): CaptureSession = ContractJson.parse("capture session") {
        ContractJson.tolerant.decodeFromString(CaptureSession.serializer(), payload)
    }

    fun parseChunk(payload: String): CaptureChunk = ContractJson.parse("capture chunk") {
        ContractJson.tolerant.decodeFromString(CaptureChunk.serializer(), payload)
    }

    fun parseChunkRequest(payload: String): CaptureChunkRequest =
        ContractJson.parse("capture-chunk request") {
            ContractJson.strict.decodeFromString(CaptureChunkRequest.serializer(), payload)
        }

    fun encodeChunkRequest(request: CaptureChunkRequest): String = ContractJson.encode {
        ContractJson.strict.encodeToString(CaptureChunkRequest.serializer(), request)
    }

    /** Encodes a read model, for fakes and tests; a model carrying UNKNOWN cannot be encoded. */
    fun encodeSession(session: CaptureSession): String = ContractJson.encode {
        ContractJson.tolerant.encodeToString(CaptureSession.serializer(), session)
    }

    /** Encodes an acknowledgement, for fakes and tests. */
    fun encodeChunk(chunk: CaptureChunk): String = ContractJson.encode {
        ContractJson.tolerant.encodeToString(CaptureChunk.serializer(), chunk)
    }
}
