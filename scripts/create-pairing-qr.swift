#!/usr/bin/env swift

import CoreImage
import Foundation
import ImageIO
import Security
import UniformTypeIdentifiers

enum PairingError: Error, CustomStringConvertible {
    case usage
    case invalidURL
    case keychain(OSStatus)
    case invalidToken
    case pairingService
    case invalidPairingResponse
    case qrGeneration
    case imageEncoding

    var description: String {
        switch self {
        case .usage:
            return "usage: create-pairing-qr.swift <https-base-url> <output.png> [keychain-service]"
        case .invalidURL:
            return "the pairing base URL must be an HTTPS origin"
        case let .keychain(status):
            return "could not read the bridge token from Keychain (status \(status))"
        case .invalidToken:
            return "the Keychain item is not a valid bridge token"
        case .pairingService:
            return "the local bridge did not create a pairing ticket"
        case .invalidPairingResponse:
            return "the local bridge returned an invalid pairing response"
        case .qrGeneration:
            return "could not generate the pairing QR code"
        case .imageEncoding:
            return "could not encode the pairing QR code as PNG"
        }
    }
}

func bridgeToken(service: String) throws -> String {
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: service,
        kSecAttrAccount as String: NSUserName(),
        kSecMatchLimit as String: kSecMatchLimitOne,
        kSecReturnData as String: true,
    ]

    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    guard status == errSecSuccess, let data = result as? Data else {
        throw PairingError.keychain(status)
    }
    guard
        let token = String(data: data, encoding: .utf8),
        token.count >= 32,
        token.rangeOfCharacter(from: .whitespacesAndNewlines) == nil
    else {
        throw PairingError.invalidToken
    }
    return token
}

struct PairingResponse: Decodable {
    let pairingTicket: String
}

func createPairingTicket(masterToken: String) throws -> String {
    let endpoint = URL(string: "http://127.0.0.1:4317/api/devices/pairing-ticket")!
    var request = URLRequest(url: endpoint)
    request.httpMethod = "POST"
    request.setValue("Bearer \(masterToken)", forHTTPHeaderField: "Authorization")
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = Data("{}".utf8)

    let semaphore = DispatchSemaphore(value: 0)
    var receivedData: Data?
    var receivedStatus: Int?
    var receivedError: Error?
    URLSession.shared.dataTask(with: request) { data, response, error in
        receivedData = data
        receivedStatus = (response as? HTTPURLResponse)?.statusCode
        receivedError = error
        semaphore.signal()
    }.resume()
    guard semaphore.wait(timeout: .now() + 10) == .success else {
        throw PairingError.pairingService
    }
    guard receivedError == nil, receivedStatus == 201, let receivedData else {
        throw PairingError.pairingService
    }
    guard
        let response = try? JSONDecoder().decode(PairingResponse.self, from: receivedData),
        response.pairingTicket.hasPrefix("pair1.")
    else {
        throw PairingError.invalidPairingResponse
    }
    return response.pairingTicket
}

func pairingURL(baseURL: String, ticket: String) throws -> URL {
    guard
        var components = URLComponents(string: baseURL),
        components.scheme == "https",
        components.host != nil,
        components.user == nil,
        components.password == nil
    else {
        throw PairingError.invalidURL
    }
    components.path = "/"
    components.query = nil
    components.fragment = "pairing=\(ticket)"
    guard let url = components.url else {
        throw PairingError.invalidURL
    }
    return url
}

func writeQRCode(contents: String, outputURL: URL) throws {
    guard
        let filter = CIFilter(name: "CIQRCodeGenerator"),
        let input = contents.data(using: .utf8)
    else {
        throw PairingError.qrGeneration
    }
    filter.setValue(input, forKey: "inputMessage")
    filter.setValue("Q", forKey: "inputCorrectionLevel")
    guard let image = filter.outputImage?.transformed(
        by: CGAffineTransform(scaleX: 10, y: 10)
    ) else {
        throw PairingError.qrGeneration
    }

    let context = CIContext(options: [.useSoftwareRenderer: true])
    guard
        let cgImage = context.createCGImage(image, from: image.extent),
        let destination = CGImageDestinationCreateWithURL(
            outputURL as CFURL,
            UTType.png.identifier as CFString,
            1,
            nil
        )
    else {
        throw PairingError.imageEncoding
    }
    CGImageDestinationAddImage(destination, cgImage, nil)
    guard CGImageDestinationFinalize(destination) else {
        throw PairingError.imageEncoding
    }
    try FileManager.default.setAttributes(
        [.posixPermissions: 0o600],
        ofItemAtPath: outputURL.path
    )
}

do {
    guard CommandLine.arguments.count == 3 || CommandLine.arguments.count == 4 else {
        throw PairingError.usage
    }
    let baseURL = CommandLine.arguments[1]
    let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
    let service = CommandLine.arguments.count == 4
        ? CommandLine.arguments[3]
        : "mobile-codex-bridge"
    let masterToken = try bridgeToken(service: service)
    let ticket = try createPairingTicket(masterToken: masterToken)
    let url = try pairingURL(baseURL: baseURL, ticket: ticket)
    try writeQRCode(contents: url.absoluteString, outputURL: outputURL)
    print("Pairing QR written to \(outputURL.path)")
} catch {
    FileHandle.standardError.write(Data("error: \(error)\n".utf8))
    exit(1)
}
