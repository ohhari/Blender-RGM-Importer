# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Add-on metadata
bl_info = {
    "name": "Blender Rgm Importer",
    "author": "ohari",
    "description": "Imports Rgm Files into Blender",
    "version": (1, 0, 0),
    "blender": (4, 2, 3),
    "location": "File > Import > Rgm (.rgm)",
    "category": "Import-Export"
}

#import pip
#pip.main(['install', 'zlib_ng', '--user'])

import bpy
from bpy.types import Operator, AddonPreferences
from bpy.props import StringProperty, IntProperty, BoolProperty, EnumProperty
import bmesh
import bpy_extras
from bpy_extras.io_utils import ImportHelper
import zlib #zlib_ng
from mathutils import Vector
import struct
import os
from pathlib import Path
import mathutils

k_texcoordScale = 1.0 / 32.0

def round(argValue):
    return int(argValue)

class Point2d:
    def __init__(self, u=0.0, v=0.0):
        self.u = u
        self.v = v

class Point3d:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = x
        self.y = y
        self.z = z
        
class Point3:
    def __init__(self, p1=0, p2=0, p3=0):
        self.p1 = p1
        self.p2 = p2
        self.p3 = p3

class Float4:
    def __init__(self, x=0.0, y=0.0, z=0.0, w=0.0):
        self.x = x
        self.y = y
        self.z = z
        self.w = w

class Colour:
    def __init__(self, r=0, g=0, b=0, a=0):
        self.r = r
        self.g = g
        self.b = b
        self.a = a

def clamp(coord, lower, upper):
    if coord.x < lower:
        coord.x = lower
    if coord.x > upper:
        coord.x = upper
    if coord.y < lower:
        coord.y = lower
    if coord.y > upper:
        coord.y = upper
    return coord

class Float2:
    def __init__(self, x=0.0, y=0.0):
        self.x = x
        self.y = y

'''
    Turns a colour value into a compressed float4
    Colour: The colour to convert
    Returns: The float4 from the colour
'''
def ConvertColourtoCompf4(colour):
    compCoord = Float4()
    compCoord.x = colour.b / 255.0
    compCoord.y = 1.0 - (colour.g / 255.0)  # invert the y coord
    compCoord.z = colour.r / 255.0
    compCoord.w = colour.a / 255.0
    return compCoord

'''
    Decompresses a UV coord
    Coord: The float4 to decompress
    Returns: A decompressed float2
'''
def DecompressTVertFloat(coord):
    decompCoord = Float2()
    coord.w = 1.0 - coord.w
    decompCoord.x = (coord.x + (coord.z * k_texcoordScale))
    decompCoord.y = (coord.y + (coord.w * k_texcoordScale))
    return decompCoord

'''
    Reads a string of length N from file.
    f - file handle
    n - length of string
    returns - the string on success, false on error
'''
def read_string_n(f, n):
    s = ""
    for i in range(n):
        b = f.read(1)
        if not b:
            return False
        s += b.decode('utf-8')
    return s

class MipLevel:
    def __init__(self):
        self.iDataLength = 0
        self.iDataLengthCompressed = 0
        self.pData = b''
        self.iWidth = 0
        self.iHeight = 0

'''
    Represents a chunk in a chunky file
    sType - eg. "FOLDMODL" or "DATAVBOL"
    iVersion - version
    sName - chunk name / info string
    iDataPosition - absolute offset in file where data begins
    iDataLength - length of data in file
    iChildCount - number of child chunks (for FOLDxxxx)
    aChildren - array of child chunks (1 through iChildCount inclusive; for FOLDxxxx)
'''
class Chunk:
    def __init__(self, currDepth=0):
        self.sType = ""
        self.iVersion = 0
        self.sName = ""
        self.iDataPosition = 0
        self.iDataLength = 0
        self.iChildCount = 0
        self.aChildren = []
        self.currDepth = currDepth

    '''
        Reads a chunk from file
        f - file handle
        returns - true on success, other values on error
    '''
    def loadFromFile(self, f):
        self.sType = f.read(8).decode('utf-8')
        #for i in range(self.currDepth):
            #print('--', end='')
        #print(self.sType, end='')       
        if self.sType is None or self.sType == "":
            print("EOF")
            return False
        self.iVersion = struct.unpack('I', f.read(4))[0]
        self.iDataLength = struct.unpack('I', f.read(4))[0]
        iStrLength = struct.unpack('I', f.read(4))[0]
        f.seek(8, 1)
        self.sName = f.read(iStrLength).decode('utf-8')
        #print(": ", self.sName)
        self.iDataPosition = f.tell()
        if self.sType[:4] == "FOLD":
            self.currDepth = self.currDepth + 1
            while f.tell() < (self.iDataPosition + self.iDataLength):
                child = Chunk(self.currDepth)
                if not child.loadFromFile(f):
                    print("Chunk Error")
                    return False
                self.aChildren.append(child)
                self.iChildCount += 1
            self.currDepth = self.currDepth - 1
        else:
            f.seek(self.iDataLength, 1)
        return True
    
    def getChildByType(self, type):
        for child in self.aChildren:
            if child.sType == type:
                return child
        return None
    
    
'''
    Represents a chunky file
    sHeader - the file header
    iVersion - version
    iChunkCount - number of root level chunks
    aChunks - array of root level chunks (1 through iChunkCount inclusive)
    fFile - the file handle
'''
class Chunky:
    def __init__(self):
        self.sHeader = ""
        self.iVersion = 0
        self.iChunkCount = 0
        self.aChunks = []
        self.fFile = None
        
        self.pMipLevels = []
        self.pData = 0
        self.iWidth = 0
        self.iHeight = 0
        self.iDataLength = 0
        self.iMipCount = 0
        self.iMipCurrent = 0
        self.iDxtCompression = 0
                   

    '''
        Reads an entire chunky file
        sName - name of file to open
        returns - true on success, other values on error
    '''
    def loadFromFile(self, sName):
        try:
            with open(sName, "rb") as fHandle:
                self.fFile = fHandle
                self.sHeader = fHandle.read(16).decode('utf-8')
                self.iVersion = struct.unpack('I', fHandle.read(4))[0]
                fHandle.seek(16, 1)
                while True:
                    chunk = Chunk(0)
                    if not chunk.loadFromFile(fHandle):
                        break
                    self.aChunks.append(chunk)
                    self.iChunkCount += 1
            print("Chunky Success")
            return True
        except Exception as error:
            print("Chunky Error: ", error)
            return False
        
    def getChunkByType(self, type):
        for chunk in self.aChunks:
            if chunk.sType == type:
                return chunk
        return None
    
    def getImageType(self):
        folderTSet = self.getChunkByType("FOLDTSET")
        if folderTSet is not None:
            folderTxtr = folderTSet.getChildByType("FOLDTXTR")
            if folderTxtr is not None:
                folderDxtc = folderTxtr.getChildByType("FOLDIMG")
                if folderDxtc is not None:
                    self.eFormat = "TGA" 
                else:
                    folderDxtc = folderTxtr.getChildByType("FOLDDXTC")
                    if folderDxtc is not None:
                        self.eFormat = "DXTC"
                    else:
                        self.eFormat = None
                        print("Cannot locate texture folder")
        return self.eFormat
    
    def loadDxtc(self, sFilename):
        folderTSet = self.getChunkByType("FOLDTSET")
        if folderTSet is not None:
            folderTxtr = folderTSet.getChildByType("FOLDTXTR")
            if folderTxtr is not None:
                folderDxtc = folderTxtr.getChildByType("FOLDDXTC")
                if folderDxtc is not None:
                    with open(sFilename, "rb") as fHandle:
                        dataTFmt = folderDxtc.getChildByType("DATATFMT")
                        if dataTFmt is not None:
                            fHandle.seek(dataTFmt.iDataPosition)
                            self.iWidth = struct.unpack('I', fHandle.read(4))[0]
                            self.iHeight = struct.unpack('I', fHandle.read(4))[0]
                            #print("Image-Size: ", self.iWidth, "x", self.iHeight)
                            fHandle.seek(8, 1)
                            self.iDxtCompression = struct.unpack('I', fHandle.read(4))[0]
                            if (self.iDxtCompression == 13) or (self.iDxtCompression == 22):
                                self.iDxtCompression = 1
                                #print("DXTC Compression: DXTC 1")
                            elif (self.iDxtCompression == 14):
                                self.iDxtCompression = 3
                                #print("DXTC Compression: DXTC 3")
                            elif (self.iDxtCompression == 15):
                                self.iDxtCompression = 5
                                #print("DXTC Compression: DXTC 5")
                            else:
                                print("Error")
                                return False
                        else:
                            print("Cannot locate DATATFMT")
                            return None
                        dataTMan = folderDxtc.getChildByType("DATATMAN")
                        if dataTMan is not None:
                            fHandle.seek(dataTMan.iDataPosition)
                            self.iMipCount = struct.unpack('I', fHandle.read(4))[0]                                          
                        else:
                            print("Cannot locate DATATMAN")  
                            return None                  
                        dataTDat = folderDxtc.getChildByType("DATATDAT")
                        if dataTDat is not None:
                            latest = fHandle.tell()
                            latest2 = dataTDat.iDataPosition
                            for iMipLevel in range(self.iMipCount):
                                fHandle.seek(latest)
                                pCurrentLevel = MipLevel()
                                pCurrentLevel.iDataLength  = struct.unpack('I', fHandle.read(4))[0]
                                iDataLengthCompressed  = struct.unpack('I', fHandle.read(4))[0]
                                latest = fHandle.tell()
                                fHandle.seek(latest2)
                                pCurrentLevel.pData += fHandle.read(iDataLengthCompressed)
                                latest2 = fHandle.tell()
                                if(iDataLengthCompressed != pCurrentLevel.iDataLength):
                                    pCurrentLevel.pData = zlib.decompress(pCurrentLevel.pData)
                                pVals = pCurrentLevel.pData
                                self.iMipCurrent = int.from_bytes(pVals[0:3], byteorder='little')
                                pCurrentLevel.iWidth = int.from_bytes(pVals[4:7], byteorder='little')
                                pCurrentLevel.iHeight = int.from_bytes(pVals[8:11], byteorder='little')
                                pCurrentLevel.iDataLength = int.from_bytes(pVals[12:15], byteorder='little')
                                self.pMipLevels.insert(0, pCurrentLevel)
                                #print("Curr. Mip-Level: ", self.iMipCurrent, " Curr. Image-Size: ",pCurrentLevel.iWidth, "x", pCurrentLevel.iHeight, " Curr. Datalength:",pCurrentLevel.iDataLength, " ")
                            
                            self.iWidth = self.pMipLevels[self.iMipCurrent].iWidth
                            self.iHeight = self.pMipLevels[self.iMipCurrent].iHeight
                            self.iDataLength = self.pMipLevels[self.iMipCurrent].iDataLength
                            self.pData = self.pMipLevels[self.iMipCurrent].pData
                            #self.pData += b'FF'# + 16
                            #print("Image decompressed")
                            #print("Image-Size: ", self.iWidth, "x", self.iHeight)
                            #print("Image-Data-Size:", self.iDataLength)
                        else:
                            print("Cannot locate DATATDAT")
                            return None                   
                else:
                    print("Cannot locate texture folder")
                    return None
            else:
                print("Cannot locate texture folder")
                return None
        else:
            print("Cannot locate tset folder")
            return None
            
    def saveDxtc(self, outFile):                            
        try:
            with open(outFile, 'wb') as fHandle:
                fHandle.write(b'DDS ')
                fHandle.write(struct.pack('I', 124))
                #N = 1 | 2 | 4 | 0x1000 | (bMipLevels ? 0x20000 : 0) | 0x80000; // DDSD_CAPS, DDSD_WIDTH, DDSD_HEIGHT, DDSD_PIXELFORMAT, DDSD_MIPMAPCOUNT, DDSD_LINEARSIZE
                if self.iMipCount > 1:
                    n = 1 | 2 | 4 | 4096 | 131072 | 524288
                else:
                    n = 1 | 2 | 4 | 4096 | 0 | 524288
                fHandle.write(struct.pack('I', n))  
                fHandle.write(struct.pack('I', self.iHeight))
                fHandle.write(struct.pack('I', self.iWidth))
                fHandle.write(struct.pack('I', self.iDataLength))
                fHandle.write(struct.pack('I', 0))
                fHandle.write(struct.pack('I', self.iMipCount))
                for i in range(11):
                    fHandle.write(struct.pack('I', 0))
                fHandle.write(struct.pack('I', 32))
                fHandle.write(struct.pack('I', 4))
                if self.iDxtCompression == 1:
                    fHandle.write(b'DXT1')
                elif self.iDxtCompression == 3:
                    fHandle.write(b'DXT3')
                elif self.iDxtCompression == 5:
                    fHandle.write(b'DXT5')
                else:
                    print("Compression Error")
                    return False
                fHandle.write(struct.pack('I', 0))
                fHandle.write(struct.pack('I', 0))
                fHandle.write(struct.pack('I', 0))
                fHandle.write(struct.pack('I', 0))
                fHandle.write(struct.pack('I', 0))
                #N = (bMipLevels ? 8 : 0) | 0x1000 | (bMipLevels ? 0x400000 : 0); // DDSCAPS_COMPLEX, DDSCAPS_TEXTURE, DDSCAPS_MIPMAP
                if self.iMipCount > 1:
                    n = 8 | 4096 | 4194304
                else:
                    n = 0 | 4096 | 0
                fHandle.write(struct.pack('I', n))
                for iMipLevel in self.pMipLevels:
                    fHandle.write(iMipLevel.pData)
                print(".dds File finished")
        except:
            print("Error while writing .rgt file ", outFile)
            
        
def importRgt(sFilename):
    if sFilename is not None:
        oRgt = Chunky()
        if not oRgt.loadFromFile(sFilename):
            print("Unable to load .rgt file ", sFilename)
        else:
            if(oRgt.getImageType() == "DXTC"):
                print("DXTC recognized, ", end='')
                oRgt.loadDxtc(sFilename)
                outFile = sFilename.split('.')[0] + ".dds"
                print(".dds file with ", oRgt.iMipCount, " mip levels will be written")
                oRgt.saveDxtc(outFile)
            else: 
                print("Invalid image type, can't be converted") 

def BytesToWeights(bytes):
    weights = [0.0] * 4
    weights[0] = (bytes[2] / 255.0 * 1000 - 0.5) / 1000
    weights[1] = (bytes[1] / 255.0 * 1000 - 0.5) / 1000
    weights[2] = (bytes[0] / 255.0 * 1000 - 0.5) / 1000
    weights[3] = (bytes[3] / 255.0 * 1000 - 0.5) / 1000
    total = sum(weights)
    delta = total - 1.0
    weights[0] -= delta
    return weights

def RgmIntoBlender_Mesh_DataData(importData, oChunk):
    with open(importData.sModelPath, "rb") as fHandle:
        fHandle.seek(oChunk.iDataPosition + 1)
        
        #holds global mesh data to compare to and collect from
        class sGlobalMeshInfo:
            def __init__(self):
                self.iVertCount = 0
                self.aVertArray = []
                self.aFaceArray = []
                self.aSkinBones = []

        #holds data for the Object identifiers
        class sObjectID:
            def __init__(self):
                self.iVertCount = 0
                self.aVertArray = []
                self.aUVArray = []
                self.iFaceCount = 0
                self.aFaceList = []
                self.aBoneIndices = []
                self.aBoneWeights = []

        #holds data for a single object 
        class sObjectStruct:
            def __init__(self):
                self.sObjectName = ""
                self.sOIDStruct = sObjectID()

        #holds all mesh data
        oMeshInfo = sGlobalMeshInfo()

        #number of separate objects
        iObjectCount = struct.unpack('I', fHandle.read(4))[0]
        aObjectList = []

        for i in range(iObjectCount):
            oObject = sObjectStruct()
            #number of faces as the verts that make them. Meshes are triangles so divide by 3
            oObject.sOIDStruct.iFaceCount = struct.unpack('I', fHandle.read(4))[0] // 3
            for j in range(oObject.sOIDStruct.iFaceCount):
                #vertex ID's given as vertnumber - 1 because of the way that C++ handles arrays (the exporter is in C++)
                aFace = Point3()
                aFace.p1 = int(struct.unpack('H', fHandle.read(2))[0]) #+1
                aFace.p2 = int(struct.unpack('H', fHandle.read(2))[0]) #+1
                aFace.p3 = int(struct.unpack('H', fHandle.read(2))[0]) #+1
                oObject.sOIDStruct.aFaceList.append(aFace)
            fHandle.seek(13, 1)
            iStrLen = struct.unpack('I', fHandle.read(4))[0]
            oObject.sObjectName = read_string_n(fHandle, iStrLen)
            aObjectList.append(oObject)

        #Per-Vertex Component Data
        iComponentCount = struct.unpack('I', fHandle.read(4))[0]

        #Per-Vertex Data
        class sPerVertStruct:
            def __init__(self):
                self.iVertComponent = 0
                self.iVectorStorageType = 0
                self.iDataType = 0

        aComponentVector = []

        for i in range(iComponentCount):
            oComponent = sPerVertStruct()
            oComponent.iVertComponent = struct.unpack('I', fHandle.read(4))[0]
            oComponent.iVectorStorageType = struct.unpack('I', fHandle.read(4))[0]
            oComponent.iDataType = struct.unpack('I', fHandle.read(4))[0]
            aComponentVector.append(oComponent)

        #Vertex Data
        class sVertStruct:
            def __init__(self):
                self.p3Position = Point3d()
                self.aBoneIndices = [1, 1, 1, 1]
                self.aBoneWeights = [0, 0, 0, 0]
                self.p3Normal = Point3d()
                self.iBiNormal = 0
                self.iTangent = 0
                self.iDiffuseColour = Colour()
                self.iSpecularColour = Colour()
                self.p3UVCh1 = Point2d()
                self.p3UVCh2 = Point2d()
                self.p3UVCh3 = Point2d()

        iVertCount = struct.unpack('I', fHandle.read(4))[0]
        iVertexSize = struct.unpack('I', fHandle.read(4))[0]
        oMeshInfo.iVertCount = iVertCount

        for i in range(iVertCount):
            oVertex = sVertStruct()
            for oComponent in aComponentVector:
                match oComponent.iVertComponent:
                    case 0:  #Position
                        oVertex.p3Position.x = -(struct.unpack('f', fHandle.read(4))[0])
                        oVertex.p3Position.y = struct.unpack('f', fHandle.read(4))[0]
                        oVertex.p3Position.z = -(struct.unpack('f', fHandle.read(4))[0])
                    case 1: #Bone Index
                        for j in range(4):
                            oVertex.aBoneIndices[j] = struct.unpack('B', fHandle.read(1))[0]
                    case 2: #Bone Weight
                        bytes = [0,0,0,0]
                        for j in range(4):
                            bytes[j] = struct.unpack('B', fHandle.read(1))[0]
                        oVertex.aBoneWeights = BytesToWeights(bytes)
                    case 3: #Normal
                        if oComponent.iDataType == 2:
                            iByte0 = float(struct.unpack('B', fHandle.read(1))[0])
                            iByte1 = float(struct.unpack('B', fHandle.read(1))[0])
                            iByte2 = float(struct.unpack('B', fHandle.read(1))[0])
                            iByte3 = float(struct.unpack('B', fHandle.read(1))[0])
                    case 4: #BiNormal
                        oVertex.iBiNormal = struct.unpack('I', fHandle.read(4))[0]
                    case 5: #Tangent
                        oVertex.iTangent = struct.unpack('I', fHandle.read(4))[0]
                    case 6: #Diffuse Vertex Color
                        oVertex.iDiffuseColour.r = struct.unpack('B', fHandle.read(1))[0]
                        oVertex.iDiffuseColour.g = struct.unpack('B', fHandle.read(1))[0]
                        oVertex.iDiffuseColour.b = struct.unpack('B', fHandle.read(1))[0]
                        oVertex.iDiffuseColour.a = struct.unpack('B', fHandle.read(1))[0]
                    case 7: #Specular Vertex Color
                        oVertex.iSpecularColour.r = struct.unpack('B', fHandle.read(1))[0]
                        oVertex.iSpecularColour.g = struct.unpack('B', fHandle.read(1))[0]
                        oVertex.iSpecularColour.b = struct.unpack('B', fHandle.read(1))[0]
                        oVertex.iSpecularColour.a = struct.unpack('B', fHandle.read(1))[0]
                    case 8: #UV Channel 1
                        if oComponent.iDataType == 2:
                            compDir = Colour()
                            compDir.r = struct.unpack('B', fHandle.read(1))[0]
                            compDir.g = struct.unpack('B', fHandle.read(1))[0]
                            compDir.b = struct.unpack('B', fHandle.read(1))[0]
                            compDir.a = struct.unpack('B', fHandle.read(1))[0]
                            Compressedfloat4 = ConvertColourtoCompf4(compDir)
                            newCoord = DecompressTVertFloat(Compressedfloat4)
                            oVertex.p3UVCh1.u = newCoord.x
                            oVertex.p3UVCh1.v = newCoord.y
                        elif oComponent.iDataType == 3:
                            oVertex.p3UVCh1.u = struct.unpack('f', fHandle.read(4))[0]
                            oVertex.p3UVCh1.v = 1.0 - struct.unpack('f', fHandle.read(4))[0]
                    case 9: #UV Channel 2
                        if oComponent.iDataType == 2:
                            compDir = Colour()
                            compDir.r = struct.unpack('B', fHandle.read(1))[0]
                            compDir.g = struct.unpack('B', fHandle.read(1))[0]
                            compDir.b = struct.unpack('B', fHandle.read(1))[0]
                            compDir.a = struct.unpack('B', fHandle.read(1))[0]
                            Compressedfloat4 = ConvertColourtoCompf4(compDir)
                            newCoord = DecompressTVertFloat(Compressedfloat4)
                            oVertex.p3UVCh2.u = newCoord.x
                            oVertex.p3UVCh2.v = newCoord.y
                        elif oComponent.iDataType == 3:
                            oVertex.p3UVCh2.u = struct.unpack('f', fHandle.read(4))[0]
                            oVertex.p3UVCh2.v = 1.0 - struct.unpack('f', fHandle.read(4))[0]
                    case 10: #UV Channel 3
                        if oComponent.iDataType == 2:
                            compDir = Colour()
                            compDir.r = struct.unpack('B', fHandle.read(1))[0]
                            compDir.g = struct.unpack('B', fHandle.read(1))[0]
                            compDir.b = struct.unpack('B', fHandle.read(1))[0]
                            compDir.a = struct.unpack('B', fHandle.read(1))[0]
                            Compressedfloat4 = ConvertColourtoCompf4(compDir)
                            newCoord = DecompressTVertFloat(Compressedfloat4)
                            oVertex.p3UVCh3.u = newCoord.x
                            oVertex.p3UVCh3.v = newCoord.y
                        elif oComponent.iDataType == 3:
                            oVertex.p3UVCh3.u = struct.unpack('f', fHandle.read(4))[0]
                            oVertex.p3UVCh3.v = 1.0 - struct.unpack('f', fHandle.read(4))[0]
            oMeshInfo.aVertArray.append(oVertex)

        iVertUnknown = struct.unpack('I', fHandle.read(4))[0]

        #Material
        iStrLen = struct.unpack('I', fHandle.read(4))[0]
        sMaterialName = read_string_n(fHandle, iStrLen)

        #Skin
        iNumSkinBones = struct.unpack('I', fHandle.read(4))[0]
        for i in range(iNumSkinBones):
            fHandle.seek(96, 1)
            iStrLen = struct.unpack('I', fHandle.read(4))[0]
            sBoneName = read_string_n(fHandle, iStrLen)
            oMeshInfo.aSkinBones.append(sBoneName)

        #Organise the verts for the per object mesh construction
        #Each object contains a list of faces.
        #Each of these faces contains 3 indexes to verticies in the global vertex list.
        #We want to copy these verticies to the local vertex/UV list and update the index to be into the local list.

        aVertexIdMap = []
        for itrObject in aObjectList:
            #The object should already be set to this, but make sure:
            itrObject.sOIDStruct.iVertCount = 0
            itrObject.sOIDStruct.aVertArray = []
            itrObject.sOIDStruct.aUVArray = []

            #Set the mapping to indicate that no global verticies have equivalent local verticies
            for i in range(oMeshInfo.iVertCount):
                aVertexIdMap.append(0)
            
            for i in range(itrObject.sOIDStruct.iFaceCount):
                iGlobalVertId = 0
                iLocalVertId = 0
                
                #X
                iGlobalVertId = itrObject.sOIDStruct.aFaceList[i].p1
                iLocalVertId = aVertexIdMap[iGlobalVertId]
                if iLocalVertId == 0:
                    itrObject.sOIDStruct.aVertArray.append(oMeshInfo.aVertArray[iGlobalVertId].p3Position)
                    itrObject.sOIDStruct.aUVArray.append(oMeshInfo.aVertArray[iGlobalVertId].p3UVCh1)
                    itrObject.sOIDStruct.aBoneIndices.extend(oMeshInfo.aVertArray[iGlobalVertId].aBoneIndices)
                    itrObject.sOIDStruct.aBoneWeights.extend(oMeshInfo.aVertArray[iGlobalVertId].aBoneWeights)
                    itrObject.sOIDStruct.iVertCount += 1
                    iLocalVertId = itrObject.sOIDStruct.iVertCount
                    aVertexIdMap[iGlobalVertId] = iLocalVertId
                itrObject.sOIDStruct.aFaceList[i].p1 = iLocalVertId-1
                
                #Y
                iGlobalVertId = itrObject.sOIDStruct.aFaceList[i].p2
                iLocalVertId = aVertexIdMap[iGlobalVertId]
                if iLocalVertId == 0:
                    itrObject.sOIDStruct.aVertArray.append(oMeshInfo.aVertArray[iGlobalVertId].p3Position)
                    itrObject.sOIDStruct.aUVArray.append(oMeshInfo.aVertArray[iGlobalVertId].p3UVCh1)
                    itrObject.sOIDStruct.aBoneIndices.extend(oMeshInfo.aVertArray[iGlobalVertId].aBoneIndices)
                    itrObject.sOIDStruct.aBoneWeights.extend(oMeshInfo.aVertArray[iGlobalVertId].aBoneWeights)
                    itrObject.sOIDStruct.iVertCount += 1
                    iLocalVertId = itrObject.sOIDStruct.iVertCount
                    aVertexIdMap[iGlobalVertId] = iLocalVertId
                itrObject.sOIDStruct.aFaceList[i].p2 = iLocalVertId-1
                
                #Z
                iGlobalVertId = itrObject.sOIDStruct.aFaceList[i].p3
                iLocalVertId = aVertexIdMap[iGlobalVertId]
                if iLocalVertId == 0:
                    itrObject.sOIDStruct.aVertArray.append(oMeshInfo.aVertArray[iGlobalVertId].p3Position)
                    itrObject.sOIDStruct.aUVArray.append(oMeshInfo.aVertArray[iGlobalVertId].p3UVCh1)
                    itrObject.sOIDStruct.aBoneIndices.extend(oMeshInfo.aVertArray[iGlobalVertId].aBoneIndices)
                    itrObject.sOIDStruct.aBoneWeights.extend(oMeshInfo.aVertArray[iGlobalVertId].aBoneWeights)
                    itrObject.sOIDStruct.iVertCount += 1
                    iLocalVertId = itrObject.sOIDStruct.iVertCount
                    aVertexIdMap[iGlobalVertId] = iLocalVertId
                itrObject.sOIDStruct.aFaceList[i].p3 = iLocalVertId-1
        
        contains_normal = False
        contains_crushed = False
        contains_wrecked = False
        contains_tread = False 
        for oTempObject in aObjectList:
            obj_name  = oTempObject.sObjectName
            mesh_data = bpy.data.meshes.new(f"{obj_name}_data")
            mesh_obj = bpy.data.objects.new(obj_name, mesh_data)
            
            if "crush" in obj_name.lower():
                if not contains_crushed:
                    crushed_collection = bpy.data.collections.new("Crushed")
                    bpy.context.scene.collection.children.link(crushed_collection)
                    contains_crushed = True
                crushed_collection.objects.link(mesh_obj)
            elif "wreck" in obj_name.lower():
                if not contains_wrecked:
                    wrecked_collection = bpy.data.collections.new("Wrecked")
                    bpy.context.scene.collection.children.link(wrecked_collection)
                    contains_wrecked = True
                wrecked_collection.objects.link(mesh_obj)
            elif "critical_tread" in obj_name.lower():
                if not contains_tread:
                    tread_collection = bpy.data.collections.new("Tread")
                    bpy.context.scene.collection.children.link(tread_collection)
                    contains_tread = True
                tread_collection.objects.link(mesh_obj)            
            else:
                if not contains_normal:
                    normal_collection = bpy.data.collections.new("Normal")
                    bpy.context.scene.collection.children.link(normal_collection)
                    contains_normal = True
                normal_collection.objects.link(mesh_obj)

            bm = bmesh.new()

            for vert_indices in oTempObject.sOIDStruct.aVertArray:

                bm.verts.new((-vert_indices.x, -vert_indices.z, vert_indices.y))

            bm.verts.ensure_lookup_table()
            bm.verts.index_update()

            for face_indices in oTempObject.sOIDStruct.aFaceList:
                bm.faces.new([bm.verts[face_indices.p1], bm.verts[face_indices.p2], bm.verts[face_indices.p3]])

            uv_layer = bm.loops.layers.uv.new()
            for face in bm.faces:
                for loop in face.loops:
                    # Get the index of the vertex the loop contains.
                    loop[uv_layer].uv = (oTempObject.sOIDStruct.aUVArray[loop.vert.index].u, oTempObject.sOIDStruct.aUVArray[loop.vert.index].v)
            
            
            bm.to_mesh(mesh_data)
            for material in bpy.data.materials:
                if material.name == sMaterialName:     
                    mesh_data.materials.append(material)
                    break
            mesh_data.shade_smooth()
            mesh_data.update()

            bm.free()

            '''if oMeshInfo.aSkinBones:
                skinMod = Skin(filter_vertices=True, filter_cross_sections=False, filter_envelopes=False,
                            draw_all_gizmos=False, envelopesAlwaysOnTop=False, crossSectionsAlwaysOnTop=False,
                            showNoEnvelopes=True)
                oMesh.addModifier(skinMod)
                max.modify_mode()s
                oMesh.select()

                for skinBoneName in oMeshInfo.aSkinBones:
                    skinBone = getNodeByName(skinBoneName, exact=True)
                    if skinBone is not None:
                        pass
                        #skinOps.addbone(oMesh.skin, skinBone, 0)

                oMesh.select()
                for i in range(itrObject.sOIDStruct.iVertCount):
                    index = itrObject.sOIDStruct.aBoneIndices[i][0]
                    if index == 0:
                        index += 1
                    #skinOps.SetVertexWeights(oMesh.skin, i, index, 1.0)

                    for j in range(1, 4):
                        if itrObject.sOIDStruct.aBoneWeights[i][j] > 0:
                            index = itrObject.sOIDStruct.aBoneIndices[i][j]
                            weight = itrObject.sOIDStruct.aBoneWeights[i][j]
                            #skinOps.SetVertexWeights(oMesh.skin, i, index, weight)

            #meshop.weldVertsByThreshold(oMesh, oMesh.verts, 0.00001)
            '''
            print("Mesh: " + oTempObject.sObjectName + " built!")
    '''
def RgmIntoBlender_FoldTrim_DataData(oRgm, oChunk, sName, mMultiMat):
    oRgm.fFile.seek(oChunk.iDataPosition)  # jump to chunk

    # Per-Vertex Component Data
    iComponentCount = struct.unpack('I', oRgm.fFile.read(4))[0]

    class sPerVertStruct:
        def __init__(self):
            self.iVertComponent = 0
            self.iStorageType = 0
            self.iDataType = 0

    aComponentVector = []

    for i in range(iComponentCount):
        oComponent = sPerVertStruct()
        oComponent.iVertComponent = struct.unpack('I', oRgm.fFile.read(4))[0]
        oComponent.iStorageType = struct.unpack('I', oRgm.fFile.read(4))[0]  # This number should usually be three.
        oComponent.iDataType = struct.unpack('I', oRgm.fFile.read(4))[0]
        aComponentVector.append(oComponent)

    # Per-Vertex Data
    class sVertStruct:
        def __init__(self):
            self.p3Position = [0, 0, 0]
            self.aBoneIndices = [0, 0, 0, 0]
            self.aBoneWeights = []
            self.iNormal = 0
            self.iBiNormal = 0
            self.iTangent = 0
            self.iDiffuseColour = [0, 0, 0, 0]
            self.iSpecularColour = [0, 0, 0, 0]
            self.p3UVCh1 = [0, 0, 0]
            self.p3UVCh2 = [0, 0, 0]
            self.p3UVCh3 = [0, 0, 0]

    aVerticies = []

    iVertCount = struct.unpack('I', oRgm.fFile.read(4))[0]
    iVertexSize = struct.unpack('I', oRgm.fFile.read(4))[0]  # number of bytes taken up by each vertex on disk.

    for i in range(iVertCount):
        oVertex = sVertStruct()

        for oComponent in aComponentVector:
            if oComponent.iVertComponent == 0:  # Position
                oVertex.p3Position[0] = -struct.unpack('f', oRgm.fFile.read(4))[0]
                oVertex.p3Position[2] = struct.unpack('f', oRgm.fFile.read(4))[0]
                oVertex.p3Position[1] = -struct.unpack('f', oRgm.fFile.read(4))[0]
            elif oComponent.iVertComponent == 1:  # Bone Index
                for j in range(4):
                    oVertex.aBoneIndices[j] = struct.unpack('B', oRgm.fFile.read(1))[0]
            elif oComponent.iVertComponent == 2:  # Bone Weight
                bytes = []
                for j in range(4):
                    bytes.append(struct.unpack('B', oRgm.fFile.read(1))[0])
                oVertex.aBoneWeights = BytesToWeights(bytes)
            elif oComponent.iVertComponent == 3:  # Normal
                oVertex.iNormal = struct.unpack('I', oRgm.fFile.read(4))[0]
            elif oComponent.iVertComponent == 4:  # BiNormal
                oVertex.iBiNormal = struct.unpack('I', oRgm.fFile.read(4))[0]
            elif oComponent.iVertComponent == 5:  # Tangent
                oVertex.iTangent = struct.unpack('I', oRgm.fFile.read(4))[0]
            elif oComponent.iVertComponent == 6:  # Diffuse Vertex Colour
                oVertex.iDiffuseColour[0] = struct.unpack('B', oRgm.fFile.read(1))[0]
                oVertex.iDiffuseColour[1] = struct.unpack('B', oRgm.fFile.read(1))[0]
                oVertex.iDiffuseColour[2] = struct.unpack('B', oRgm.fFile.read(1))[0]
                oVertex.iDiffuseColour[3] = struct.unpack('B', oRgm.fFile.read(1))[0]
            elif oComponent.iVertComponent == 7:  # Specular Vertex Colour
                oVertex.iSpecularColour[0] = struct.unpack('B', oRgm.fFile.read(1))[0]
                oVertex.iSpecularColour[1] = struct.unpack('B', oRgm.fFile.read(1))[0]
                oVertex.iSpecularColour[2] = struct.unpack('B', oRgm.fFile.read(1))[0]
                oVertex.iSpecularColour[3] = struct.unpack('B', oRgm.fFile.read(1))[0]
            elif oComponent.iVertComponent == 8:  # UV Channel 1
                if oComponent.iDataType == 2:
                    compDir = [0, 0, 0, 0]
                    compDir[0] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    compDir[1] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    compDir[2] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    compDir[3] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    Compressedfloat4 = ConvertColourtoCompf4(compDir)
                    newCoord = DecompressTVertFloat(Compressedfloat4)
                    oVertex.p3UVCh1[0] = newCoord[0]
                    oVertex.p3UVCh1[1] = newCoord[1]
                elif oComponent.iDataType == 3:
                    oVertex.p3UVCh1[0] = struct.unpack('f', oRgm.fFile.read(4))[0]
                    oVertex.p3UVCh1[1] = 1.0 - struct.unpack('f', oRgm.fFile.read(4))[0]
            elif oComponent.iVertComponent == 9:  # UV Channel 2
                if oComponent.iDataType == 2:
                    compDir = [0, 0, 0, 0]
                    compDir[0] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    compDir[1] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    compDir[2] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    compDir[3] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    Compressedfloat4 = ConvertColourtoCompf4(compDir)
                    newCoord = DecompressTVertFloat(Compressedfloat4)
                    oVertex.p3UVCh2[0] = newCoord[0]
                    oVertex.p3UVCh2[1] = newCoord[1]
                elif oComponent.iDataType == 3:
                    oVertex.p3UVCh2[0] = struct.unpack('f', oRgm.fFile.read(4))[0]
                    oVertex.p3UVCh2[1] = 1.0 - struct.unpack('f', oRgm.fFile.read(4))[0]
            elif oComponent.iVertComponent == 10:  # UV Channel 3
                if oComponent.iDataType == 2:
                    compDir = [0, 0, 0, 0]
                    compDir[0] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    compDir[1] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    compDir[2] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    compDir[3] = struct.unpack('B', oRgm.fFile.read(1))[0]
                    Compressedfloat4 = ConvertColourtoCompf4(compDir)
                    newCoord = DecompressTVertFloat(Compressedfloat4)
                    oVertex.p3UVCh3[0] = newCoord[0]
                    oVertex.p3UVCh3[1] = newCoord[1]
                elif oComponent.iDataType == 3:
                    oVertex.p3UVCh3[0] = struct.unpack('f', oRgm.fFile.read(4))[0]
                    oVertex.p3UVCh3[1] = 1.0 - struct.unpack('f', oRgm.fFile.read(4))[0]

        aVerticies.append(oVertex)

    oRgm.fFile.seek(8, 1)  # skip unknown

    # Faces and Material IDs
    aFaces = []
    iVertsPerFace = struct.unpack('I', oRgm.fFile.read(4))[0]  # number of verts in a face
    iFaceCount = struct.unpack('I', oRgm.fFile.read(4))[0]
    iFaceCount = iFaceCount // iVertsPerFace

    for i in range(iFaceCount):
        aFace = [0, 0, 0]
        aFace[0] = struct.unpack('h', oRgm.fFile.read(2))[0] + 1
        aFace[2] = struct.unpack('h', oRgm.fFile.read(2))[0] + 1
        aFace[1] = struct.unpack('h', oRgm.fFile.read(2))[0] + 1
        aFaces.append(aFace)

    iStrLen = struct.unpack('I', oRgm.fFile.read(4))[0]
    sMaterialName = read_string_n(oRgm.fFile, iStrLen)
    iMatIDFace = 0
    iMatIDFace = aMatIDArray.index(sMaterialName)

    # Skin
    aSkinBones = []
    iNumSkinBones = struct.unpack('I', oRgm.fFile.read(4))[0]
    for i in range(iNumSkinBones):
        oRgm.fFile.seek(96, 1)  # Skip two transform matrices (we don't need that in MAX)
        iStrLen = struct.unpack('I', oRgm.fFile.read(4))[0]
        sBoneName = read_string_n(oRgm.fFile, iStrLen)
        aSkinBones.append(sBoneName)

    # Sort verts into arrays
    aBuiltVertArray = []
    aBuiltUVArray = []

    for Vert in aVerticies:
        aBuiltVertArray.append(Vert.p3Position)
        aBuiltUVArray.append(Vert.p3UVCh1)

    # Construct Mesh
    oMesh = mesh(vertices=aBuiltVertArray, faces=aFaces)  # construct mesh. oMesh = current mesh handle.
    oMesh.name = sName  # set object name
    oMesh.wireColor = (0, 0, 0)  # set wire colour to black
    oMesh.material = mMultiMat  # assign material
    update(oMesh)
    deselect(oMesh)
    setMesh(oMesh, tverts=aBuiltUVArray)
    buildTVFaces(oMesh)

    for i in range(oMesh.numfaces):
        oFace = getFace(oMesh, i)
        setTVFace(oMesh, i, oFace[0], oFace[1], oFace[2])
        setFaceSmoothGroup(oMesh, i, 0)  # clear smoothing groups (o = none)
        setFaceMatID(oMesh, i, iMatIDFace)  # set the material ID

    # Create Smooth Modifier
    mod_smooth = Smooth(autosmooth=True, threshold=fSmoothAngle)
    addModifier(oMesh, mod_smooth)
    collapseStack(oMesh)

    # Create skin
    if len(aSkinBones) > 0:
        # Create skin modifier
        modskin = Skin(filter_vertices=True, filter_cross_sections=False, filter_envelopes=False,
                       draw_all_gizmos=False, envelopesAlwaysOnTop=False, crossSectionsAlwaysOnTop=False,
                       showNoEnvelopes=True)

        # Add skin modifier to mesh
        addModifier(oMesh, modskin)
        max_modify_mode()
        select(oMesh)

        # Add bones to skin modifier
        for skinBone in aSkinBones:
            skinOps.addbone(oMesh.skin, getNodeByName(skinBone, exact=True), 0)

        # Set vertex weights
        select(oMesh)
        for i in range(len(aVerticies)):
            skinOps.SetVertexWeights(oMesh.skin, i, aVerticies[i].aBoneIndices[0] + 1, 1.0)
            for j in range(1, 4):
                if aVerticies[i].aBoneIndices[j] > 0:
                    index = aVerticies[i].aBoneIndices[j] + 1
                    weight = aVerticies[i].aBoneWeights[j]
                    skinOps.SetVertexWeights(oMesh.skin, i, index, weight)

    # Weld vertices
    meshop.weldVertsByThreshold(oMesh, oMesh.verts, 0.00001)

    print("Mesh: " + oMesh.name + " built!")
'''
def RgmIntoBlender_FoldMrgm(importData, oChunk):
    iDataDataCount = 0
    i = 0
    while i < oChunk.iChildCount:
        if oChunk.aChildren[i].sType == "DATADATA":
            if iDataDataCount == 0:
                print("Mesh-Data found")
                RgmIntoBlender_Mesh_DataData(importData, oChunk.aChildren[i])
                iDataDataCount = 1
        i = i + 1

def RgmIntoBlender_FoldMgrp(importData, oChunk):
    i = 0
    while i < oChunk.iChildCount:
        if oChunk.aChildren[i].sType == "FOLDMESH":
            print("Mesh-Folder found")
            RgmIntoBlender_FoldMesh(importData, oChunk.aChildren[i])
        i = i + 1


def RgmIntoBlender_FoldMesh(importData, oChunk):
    i = 0
    while i < oChunk.iChildCount:
        if oChunk.aChildren[i].sType == "FOLDMGRP":
            print("Mgrp-Folder found")
            RgmIntoBlender_FoldMgrp(importData, oChunk.aChildren[i])
        elif oChunk.aChildren[i].sType == "FOLDMRGM":
            print("Mrgm-Folder found")    
            RgmIntoBlender_FoldMrgm(importData, oChunk.aChildren[i])
        elif oChunk.aChildren[i].sType == "FOLDTRIM":
            print("Trim-Folder found")
            RgmIntoBlender_FoldTrim(importData, oChunk.aChildren[i])
        i = i + 1

def RgmIntoBlender_FoldTrim(importData, oChunk):
    iDataDataCount = 0
    i = 1
    while i < oChunk.iChildCount:
        if oChunk.aChildren[i].sType == "DATADATA":
            if iDataDataCount == 0:
                print("Trim-Data found")
                #RgmIntoBlender_FoldTrim_DataData(oRgm, oChunk.aChildren[i], oChunk.sName)
                iDataDataCount = 1
        i = i + 1

def RgmIntoBlender_FoldMesh_FoldSkel(importData, oChunk): #To Do
    with open(importData.sModelPath, "rb") as fHandle:    
        # Read number of bones in the skeleton
        dataInfo = oChunk.aChildren[0]
        fHandle.seek(dataInfo.iDataPosition)
        numBones = struct.unpack('I', fHandle.read(4))[0]

        armature = bpy.data.armatures.new('Armature')
        arm_object = bpy.data.objects.new('Armature Object', armature)

        bpy.context.collection.objects.link(arm_object)

        bpy.context.view_layer.objects.active = arm_object
        bpy.ops.object.mode_set(mode='EDIT', toggle=False)
        edit_bones = arm_object.data.edit_bones
        bone_array = []

        #mat = mathutils.Matrix(((-1, 0, 0, 0), (0, -1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)))
        #imat = mathutils.Matrix(((-1, 0, 0, 0), (0, -1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)))

        #mat = mathutils.Matrix(((1, 0, 0, 0), (-1, -1, -1, -1), (-1, -1, -1, -1), (0, 0, 0, 1)))
        #imat = mathutils.Matrix(((-1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)))

        # Read bones
        for k in range(numBones):
            dataBone = oChunk.aChildren[k + 1]
            fHandle.seek(dataBone.iDataPosition)

            # Read parent id
            parent = struct.unpack('i', fHandle.read(4))[0]

            # Skip unknown value
            fHandle.seek(4, 1)
            # Read bone transforms
            matrix = mathutils.Matrix(((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 1)))
            for i in range(4):
                for j in range(3):
                    matrix[j][i] = struct.unpack('f', fHandle.read(4))[0]

            #matrix = mathutils.Matrix(((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 1)))
            #matrix[0] = (-matrix_in[0][0], -matrix_in[0][2], -matrix_in[0][1], -matrix_in[0][3])
            #matrix[1] = (-matrix_in[2][0], matrix_in[2][2], matrix_in[2][1], matrix_in[2][3])
            #matrix[2] = (-matrix_in[1][0], matrix_in[1][2], matrix_in[1][1], matrix_in[1][3])

            # Create bone
            b = edit_bones.new(dataBone.sName)
            #b.head = (0.0, 0.0, 0.0)
            b.tail = (0.0, 0.0, 1.0)
            #newbone.boxSize = [0.05, 0.05, 0.05]
            #newbone.wireColor = (255, 255, 0)

            # Store new bone
            bone_array.append(b)

            # Set bone parent & transforms
            if parent >= 0:
                #tworld = sworld * transf. matrix * sworld inverse
                print("Name: ", dataBone.sName)
                print("ID: ", k)
                print("ParentID: ", parent)
                #print(matrix)
                #matrix = imat @ matrix @ mat
                print("Matrix: ", matrix)
                #print("Bonematrix: ", b.matrix)
                b.parent = bone_array[parent]

                print("Parent Bonematrix: ", bone_array[parent].matrix)
                matrix_i1 = mathutils.Matrix(((0, 0, 0), (0, 0, 0), (0, 0, 0)))
                matrix_i1[0] = (matrix[0][0], matrix[0][1], matrix[0][2])
                matrix_i1[1] = (matrix[1][0], matrix[1][1], matrix[1][2])
                matrix_i1[2] = (matrix[2][0], matrix[2][1], matrix[2][2])

                matrix_p = bone_array[parent].matrix
                matrix_i2 = mathutils.Matrix(((0, 0, 0), (0, 0, 0), (0, 0, 0)))
                matrix_i2[0] = (matrix_p[0][0], matrix_p[0][1], matrix_p[0][2])
                matrix_i2[1] = (matrix_p[1][0], matrix_p[1][1], matrix_p[1][2])
                matrix_i2[2] = (matrix_p[2][0], matrix_p[2][1], matrix_p[2][2])

                matrix_m = mathutils.Matrix(((0, 0, 0), (0, 0, 0), (0, 0, 0)))
                matrix_m = matrix_i1 @ matrix_i2

                matrix_o = mathutils.Matrix(((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 1)))
                matrix_o[0] = (matrix_m[0][0], matrix_m[0][1], matrix_m[0][2], -matrix[0][3] + matrix_p[0][3])
                matrix_o[1] = (matrix_m[1][0], matrix_m[1][1], matrix_m[1][2], matrix[1][3] + matrix_p[1][3])
                matrix_o[2] = (matrix_m[2][0], matrix_m[2][1], matrix_m[2][2], matrix[2][3] + matrix_p[2][3])

                #Alternativ
                #matrix_o[0] = (matrix_m[0][0], matrix_m[0][1], matrix_m[0][2], matrix[0][3] + matrix_p[0][3])
                #matrix_o[1] = (matrix_m[1][0], matrix_m[1][1], matrix_m[1][2], -matrix[1][3] + matrix_p[1][3])
                #matrix_o[2] = (matrix_m[2][0], matrix_m[2][1], matrix_m[2][2], matrix[2][3] + matrix_p[2][3])

                #print(matrix_o)
                b.transform(matrix_o)#matrix @ bone_array[parent].matrix)
                #print("Matrix @ Parent Matrix: ", matrix_o)#matrix @ bone_array[parent].matrix)
                #print("Neue Bonematrix: ", b.matrix)
            else:
                print("Name: ", dataBone.sName)
                print("ID: ", k)
                #print(matrix)
                matrix_o = mathutils.Matrix(((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 1)))
                matrix_o[0] = (matrix[0][0], matrix[0][2], matrix[0][1], matrix[0][3])
                matrix_o[1] = (matrix[2][0], matrix[2][2], matrix[2][1], matrix[2][3])
                matrix_o[2] = (matrix[1][0], matrix[1][2], matrix[1][1], matrix[1][3])
                #print(matrix_o)
                b.transform(matrix_o)# @ mathutils.Matrix(((-1, 0, 0, 0), (0, -1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)))#transformation in xzy koordinatensystem
                #print(b.matrix)

            #Bone created
            #print("Bone: " + dataBone.sName + " created!")
        bpy.ops.object.mode_set(mode='OBJECT')
            
def RgmIntoBlender_FoldModl_DataMrks(importData, oChunk): #To Do
    with open(importData.sModelPath, "rb") as fHandle:    
        fHandle.seek(oChunk.iDataPosition)

        # Read number of markers
        numMarkers = struct.unpack('I', fHandle.read(4))[0]
        if numMarkers == 0:
            print("No markers found")

        # Read markers
        for i in range(numMarkers):
            # Read marker name
            len = struct.unpack('I', fHandle.read(4))[0]
            name = read_string_n(fHandle, len)
            #print(name)

            # Read marker parent name
            len = struct.unpack('I', fHandle.read(4))[0]
            parent = read_string_n(fHandle, len)

            # Read marker transform matrix
            mat = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
            mat[0] = [struct.unpack('f', fHandle.read(4))[0], struct.unpack('f', fHandle.read(4))[0], struct.unpack('f', fHandle.read(4))[0]]
            mat[1] = [struct.unpack('f', fHandle.read(4))[0], struct.unpack('f', fHandle.read(4))[0], struct.unpack('f', fHandle.read(4))[0]]
            mat[2] = [struct.unpack('f', fHandle.read(4))[0], struct.unpack('f', fHandle.read(4))[0], struct.unpack('f', fHandle.read(4))[0]]
            mat[3] = [struct.unpack('f', fHandle.read(4))[0], struct.unpack('f', fHandle.read(4))[0], struct.unpack('f', fHandle.read(4))[0]]
            #material = bpy.types.Point.new(oChunk.sName)
            # Create marker
            #marker = Point(size=10)
            #marker.wireColor = (14, 255, 2)
            #marker.name = name
            #marker.parent = getNodeByName(parent, exact=True)

            # Read number of parameters
            numParams = struct.unpack('I', fHandle.read(4))[0]

            # Read parameters
            for j in range(numParams):
                # Read parameter key
                len = struct.unpack('I', fHandle.read(4))[0]
                paramKey = read_string_n(fHandle, len)

                # Read unknown value (11)
                unknown = struct.unpack('I', fHandle.read(4))[0]

                # Read parameter value
                len = struct.unpack('I', fHandle.read(4))[0]
                paramValue = read_string_n(fHandle, len)

                # Add parameter to marker's User Defines properties
                #setUserProp(marker, paramKey, paramValue)

            # Set marker transforms
            if parent != "" and parent is not None:
                # Marker has a parent (transform in parent space)
                #pos = mat.translationpart
                #pos[0] *= -1
                #rot = mat.rotationpart
                #rot[0] *= -1
                #in_coordsys(parent, marker.rotation = rot)
                #in_coordsys(parent, marker.pos = pos)
                pass
            else:
                pass
                # Marker has no parent (transform in world space)
                #marker.transform = mat @ ([[1, 0, 0], [0, 0, 1], [0, -1, 0], [0, 0, 0]])

            # Adjust marker scale (for zooming)
            #marker.scale = [0.01, 0.01, 0.01]

            # Marker created
            #print("Marker: " + name + " created!")

def RgmIntoBlender_FoldMtrl(importData, oChunk):
    diffPath = ""
    normPath = ""

    with open(importData.sModelPath, "rb") as fHandle:
        i = 0
        while i < oChunk.iChildCount:
            if oChunk.aChildren[i].sType == "DATAINFO":
                fHandle.seek(oChunk.aChildren[i].iDataPosition)
                iStrLen = struct.unpack('I', fHandle.read(4))[0]
                sShaderName = read_string_n(fHandle, iStrLen)
                print("Shader: " + sShaderName)
            elif "VAR" in oChunk.aChildren[i].sType:
                fHandle.seek(oChunk.aChildren[i].iDataPosition)
                iStrLen = struct.unpack('I', fHandle.read(4))[0]
                sTexType = read_string_n(fHandle, iStrLen)
                print("Texture type: " + sTexType, end='')
                fHandle.seek(4, 1)
                iStrLen = struct.unpack('I', fHandle.read(4))[0]
                if sTexType == "diffusetex":          
                    if importData.importDirectory == 'Work':
                        sTexPath = read_string_n(fHandle, iStrLen).rsplit('\\', 1)[1].split('.')[0].rstrip('\x00')
                        diffPath = importData.sWorkingDirectory + "/" + sTexPath + ".dds"
                    elif importData.importDirectory == 'Asset':
                        sTexPath = read_string_n(fHandle, iStrLen).split('.')[0].rstrip('\x00')
                        diffPath = importData.sAssetDirectory + "/data/" + sTexPath.replace('\\', '/') + ".dds"
                    print(", path: " + diffPath)
                elif sTexType == "normalmap":
                    if importData.importDirectory == 'Work':
                        sTexPath = read_string_n(fHandle, iStrLen).rsplit('\\', 1)[1].split('.')[0].rstrip('\x00')
                        normPath = importData.sWorkingDirectory + "/" + sTexPath + ".dds"
                    elif importData.importDirectory == 'Asset':
                        sTexPath = read_string_n(fHandle, iStrLen).split('.')[0].rstrip('\x00')
                        normPath = importData.sAssetDirectory + "/data/" + sTexPath.replace('\\', '/') + ".dds"
                    print(", path: " + normPath)
                else:
                    print("")
            i = i + 1    
    
    importError = False
    #Check for diffuse image 
    if os.path.isfile(diffPath):
        print("Image " + diffPath + " found")
    else:
        if os.path.isfile(diffPath.split('.')[0] + ".rgt"):
            importRgt(diffPath.split('.')[0] + ".rgt")
            if os.path.isfile(diffPath):
                print("Image " + diffPath.split('.')[0] + ".rgt" + " found and imported")
            else:
                importError = True
                print("Image " + diffPath.split('.')[0] + ".rgt" + " found, import failed")
        else:
            importError = True
            print("No diffuse image found, material import failed")
            
    #Check for normal image           
    if os.path.isfile(normPath):
        print("Image " + normPath + " found")
    else:
        if os.path.isfile(normPath.split('.')[0] + ".rgt"):
            importRgt(normPath.split('.')[0] + ".rgt")
            if os.path.isfile(normPath):
                print("Image " + normPath.split('.')[0] + ".rgt" + " found and imported")
            else:
                importError = True
                print("Image " + normPath.split('.')[0] + ".rgt" + " found, import failed")
        else:
            importError = True
            print("No normal image found, material import failed")         
            

    if importError == False:
        print("Material Name: " + oChunk.sName)          

        material = bpy.data.materials.new(oChunk.sName)
        material.use_nodes = True
        
        BsdfNode = material.node_tree.nodes.get('Principled BSDF')
        DiffImageNode = material.node_tree.nodes.new('ShaderNodeTexImage')
        NormImageNode = material.node_tree.nodes.new('ShaderNodeTexImage')
        NormMapNode = material.node_tree.nodes.new('ShaderNodeNormalMap')
        SepColorNode = material.node_tree.nodes.new('ShaderNodeSeparateColor')
        CombColorNode = material.node_tree.nodes.new('ShaderNodeCombineColor')
        SubtractGlossNode = material.node_tree.nodes.new('ShaderNodeMath')
        SubtractSpecNode = material.node_tree.nodes.new('ShaderNodeMath')
        
        try:
            DiffImage = bpy.data.images.load(diffPath, check_existing=True)
            DiffImageNode.image = DiffImage
            DiffImageNode.image.colorspace_settings.is_data = False
            print("Image " +  diffPath + " loaded")
        except:
            print("Image " +  diffPath + " could not be loaded")
        
        try:
            NormImage = bpy.data.images.load(normPath, check_existing=True)
            NormImageNode.image = NormImage
            NormImageNode.image.colorspace_settings.is_data = True
            print("Image " +  normPath + " loaded")
        except:
            print("Image " +  normPath + " could not be loaded")
                                          
        DiffImageNode.location = Vector((-1250.0, 400.0))
        NormImageNode.location = Vector((-1250.0, 50.0))
        NormMapNode.location = Vector((-250.0, 200.0))
        SepColorNode.location = Vector((-750.0, 0.0))
        CombColorNode.location = Vector((-500.0, 200.0))
        SubtractGlossNode.location = Vector((-250.0, 0.0))
        SubtractSpecNode.location = Vector((-250.0, 400.0))
        
        SubtractSpecNode.operation = 'SUBTRACT'
        SubtractGlossNode.operation = 'SUBTRACT'
        
        CombColorNode.inputs[2].default_value = 1.0
        SubtractSpecNode.inputs[0].default_value = 1.0
        SubtractGlossNode.inputs[0].default_value = 1.0
        
        material.node_tree.links.new(BsdfNode.inputs[0], DiffImageNode.outputs[0])
        material.node_tree.links.new(BsdfNode.inputs[2], SubtractGlossNode.outputs[0])
        material.node_tree.links.new(BsdfNode.inputs[4], DiffImageNode.outputs[1])
        material.node_tree.links.new(BsdfNode.inputs[5], NormMapNode.outputs[0])
        material.node_tree.links.new(BsdfNode.inputs[12], SubtractSpecNode.outputs[0])
        material.node_tree.links.new(NormMapNode.inputs[1], CombColorNode.outputs[0])
        material.node_tree.links.new(SubtractGlossNode.inputs[1], SepColorNode.outputs[2])
        material.node_tree.links.new(SubtractSpecNode.inputs[1], SepColorNode.outputs[0])
        material.node_tree.links.new(CombColorNode.inputs[0], NormImageNode.outputs[1])
        material.node_tree.links.new(CombColorNode.inputs[1], SepColorNode.outputs[1])
        material.node_tree.links.new(SepColorNode.inputs[0], NormImageNode.outputs[0])

def RgmIntoBlender_FoldModl(importData, oChunk):#, mMultiMat):
    # Import skeleton first
    i = 0
    while i < oChunk.iChildCount:
        if oChunk.aChildren[i].sType == "FOLDSKEL":
            print("Skeleton-Folder found")
            RgmIntoBlender_FoldMesh_FoldSkel(importData, oChunk.aChildren[i])
        i = i + 1

    # Import the rest
    i = 0
    while i < oChunk.iChildCount:
        if oChunk.aChildren[i].sType == "FOLDTSET":
            print("Texture-Folder found")
            #print("Location: ",oChunk.aChildren[i].sName)
        elif oChunk.aChildren[i].sType == "FOLDMESH":
            print("Mesh-Folder found")
            #RgmIntoBlender_FoldMesh(importData, oChunk.aChildren[i])
        elif oChunk.aChildren[i].sType == "DATAMRKS":
            print("Datamarks found")
            #RgmIntoBlender_FoldModl_DataMrks(importData, oChunk.aChildren[i])
        elif oChunk.aChildren[i].sType == "FOLDMTRL":
            print("Material-Folder found")
            if importData.importTextures == True:
                RgmIntoBlender_FoldMtrl(importData, oChunk.aChildren[i])
        i = i + 1

def RgmIntoBlender(importData, oRgm):
    i = 0
    while i < oRgm.iChunkCount:
        if oRgm.aChunks[i].sType == "FOLDMODL":
            RgmIntoBlender_FoldModl(importData, oRgm.aChunks[i])
        i = i + 1

'''
def RgaIntoMax_FoldAnim(oRga, oChunk, sFirstFrame):
    class sChannelInfo:
        def __init__(self, name, type, dataType, subType=None, object=None, frameValueOffset=None, frameTimeOffset=None, unknown=None):
            self.name = name
            self.type = type
            self.subType = subType
            self.dataType = dataType
            self.object = object
            self.frameValueOffset = frameValueOffset
            self.frameTimeOffset = frameTimeOffset
            self.unknown = unknown
            self.keys = []

    class sKeyFrame:
        def __init__(self, time=0, rotation=None, position=None):
            self.time = time
            self.rotation = rotation
            self.position = position

    # Get first chunk
    foldCmps = oChunk.aChildren[1]

    # Read animation info
    dataInfo = foldCmps.aChildren[1]
    fseek(oRga.fFile, dataInfo.iDataPosition, SEEK_SET)
    duration = ReadFloat(oRga.fFile)

    # Read animations channels
    dataChrc = foldCmps.aChildren[2]
    fseek(oRga.fFile, dataChrc.iDataPosition, SEEK_SET)

    # Read number of channels
    numChannels = ReadLong(oRga.fFile, unsigned=True)

    # Read animation data size
    dataSize = ReadLong(oRga.fFile, unsigned=True)

    # Read channels
    channels = [None] * numChannels
    for i in range(numChannels):
        # Read channel
        channel = sChannelInfo()
        tokens = filterString(read_string_n(oRga.fFile, ReadLong(oRga.fFile, unsigned=True)), ":")
        channel.type = tokens[1]
        channel.name = tokens[2]
        channel.dataType = ReadLong(oRga.fFile, unsigned=True)
        channel.keys = [None] * ReadLong(oRga.fFile, unsigned=True)
        channel.frameValueOffset = ReadLong(oRga.fFile, unsigned=True)
        channel.frameTimeOffset = ReadLong(oRga.fFile, unsigned=True)
        channel.unknown = ReadFloat(oRga.fFile)

        # Find channel object
        if channel.type == "bone":
            bones = getNodeByName(channel.name, exact=True, ignoreCase=True, all=True)
            for bone in bones:
                if channel.object is None and bone.boneEnable:
                    channel.object = bone

        elif channel.type == "material":
            tokens = filterString(channel.name, ":")
            channel.subType = tokens[3]
            multiMat = getMeditMaterial(1)
            for mat in multiMat.materialList:
                if channel.object is None and mat.name == tokens[1]:
                    channel.object = mat

        # Store channel
        channels[i] = channel

    # Setup time configuration
    stopAnimation()
    frameRate = 30
    animationRange = interval(0, (duration * frameRate - 1))
    timeConfiguration.playbackSpeed = 3
    sliderTime = 0

    # Initialize matrices
    mat = [[-1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 0]]
    imat = inverse(mat)

    # Read key frames
    for channel in channels:
        if channel.dataType != 3 and channel.dataType != 4:
            print("WARNING: Channel '" + channel.name + "': unsupported data type (" + str(channel.dataType) + ")")

        # Skip 48 bytes for type 5 (unknown)
        if channel.dataType == 5:
            fseek(oRga.fFile, 48, SEEK_CUR)

        # Read key frame values
        for i in range(channel.keys.count):
            # Create key frame
            keyFrame = sKeyFrame()

            # Read key frame
            if channel.dataType == 0:  # Material param value
                keyFrame.position = ReadFloat(oRga.fFile)

            elif channel.dataType == 3:  # Rotation
                keyFrame.rotation = quat(ReadFloat(oRga.fFile), ReadFloat(oRga.fFile), ReadFloat(oRga.fFile), ReadFloat(oRga.fFile))

            elif channel.dataType == 4:  # Rotation, Position
                keyFrame.rotation = quat(ReadFloat(oRga.fFile), ReadFloat(oRga.fFile), ReadFloat(oRga.fFile), ReadFloat(oRga.fFile))
                keyFrame.position = point3(ReadFloat(oRga.fFile), ReadFloat(oRga.fFile), ReadFloat(oRga.fFile))

            elif channel.dataType == 5:  # Unknown
                keyFrame.position = ReadFloat(oRga.fFile)

            # Store key frame
            channel.keys[i] = keyFrame

        # Animate
        with animate(on=True):
            for i in range(channel.keys.count):
                # Get key frame
                keyFrame = channel.keys[i]

                # Read key frame time
                keyFrame.time = ReadFloat(oRga.fFile)

                # Set frame
                if channel.object is not None and (sFirstFrame == "false" or i == 1):
                    frame = int(keyFrame.time * (duration * frameRate - 1))
                    at(time=frame):
                        if channel.dataType == 0:
                            for map in channel.object.maps:
                                if map is not None:
                                    if channel.subType == "diffuse_offsetu":
                                        map.coords.U_Offset = -keyFrame.position
                                    elif channel.subType == "diffuse_offsetv":
                                        map.coords.V_Offset = -keyFrame.position
                        elif channel.dataType == 3:
                            matrix = keyFrame.rotation as matrix3
                            if channel.object.parent is not None:
                                matrix = imat * matrix * mat
                                in coordsys parent channel.object.rotation = matrix.rotation
                            else:
                                matrix *= [[1, 0, 0], [0, 0, 1], [0, -1, 0], [0, 0, 0]]
                                channel.object.rotation = matrix.rotation
                        elif channel.dataType == 4:
                            matrix = keyFrame.rotation as matrix3
                            matrix.position = keyFrame.position
                            if channel.object.parent is not None:
                                matrix = imat * matrix * mat
                                in coordsys parent channel.object.rotation = matrix.rotation
                                in coordsys parent channel.object.pos = matrix.position
                            else:
                                mtrx = [[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]]
                                mtrx.row1 = [matrix[1][1], matrix[3][1], -matrix[2][1]]
                                mtrx.row2 = [-matrix[1][2], -matrix[3][2], matrix[2][2]]
                                mtrx.row3 = [-matrix[1][3], -matrix[3][3], matrix[2][3]]
                                mtrx.row4 = [-matrix[4][1], -matrix[4][3], matrix[4][2]]
                                channel.object.transform = mtrx
                        elif channel.dataType == 5:
                            # TODO
                            pass

    # Add stale modifier to unused bones
    if Relic_SceneCAs is not None:
        for obj in $objects:
            if obj.boneEnable:
                if obj.pos.controller.keys.count == 0 and obj.rotation.controller.keys.count == 0:
                    CustAttributes.add(obj, Relic_SceneCAs[20], BaseObject=True)
    else:
        print("WARNING: Relic Custom Attributes not found! Stale modifier will not be applied.")

def rga_into_max(oRga, sSavePath):
    sRefFilePath = os.path.join(sSavePath, "Model", "Reference.max")
    sAnimationPath = os.path.join(sSavePath, "Animations")

    # Get root chunk
    foldModl = oRga.aChunks[2]

    # Get number of animations
    dataInfo = foldModl.aChildren[1]
    oRga.fFile.seek(dataInfo.iDataPosition)
    oRga.fFile.seek(20, os.SEEK_CUR)
    numAnimations = read_long(oRga.fFile)  # unsigned

    # Read animations
    for i in range(1, numAnimations + 1):
        foldAnim = foldModl.aChildren[i + 1]

        # Load reference model
        load_max_file(sRefFilePath)

        # Read animation
        rga_into_max_fold_anim(oRga, foldAnim)

        # Create sub directories (if needed)
        os.makedirs(os.path.join(sAnimationPath, get_filename_path(foldAnim.sName)), exist_ok=True)

        # Save animation
        save_max_file(os.path.join(sAnimationPath, f"{foldAnim.sName}.max"))  # useNewFile: False

        # Update progress
        progress_update(100.0 * i / numAnimations)

        # Animation imported
        print(f"Animation: {foldAnim.sName} imported!")

    # Load back reference model
    load_max_file(sRefFilePath)

'''
# Graphical user interface stuff
class ImportRgm():
    def __init__(self) -> None:
        self.resetScene = False
        self.importTextures = False
        self.importAnimations = False
        self.importDirectory = 'Work'
        self.sAssetDirectory = "C:/Users/Carsten/Desktop/coh/CoH2" #assets/data" #organized COH file directory     
        self.sWorkingDirectory = "" #rgm file directory
        self.debug = True
        
        self.sModelName = "" #panzerfaust
        self.sModelPath = "" #.rgm
        
    def setData(self, resetScene, modelPath, importTextures, importAnimations, importDirectory):
        self.resetScene = resetScene
        self.importTextures = importTextures
        self.importAnimations = importAnimations
        self.importDirectory = importDirectory
        self.sWorkingDirectory = os.path.dirname(modelPath).replace('\\', '/')
        #directory = os.path.dirname(os.path.abspath(sFilename)).replace('\\', '/')
        self.sModelPath = modelPath
        self.sModelName = Path(modelPath).stem

    def loadRgm(self):
        if self.resetScene:
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.delete(use_global=False)
            for collection in bpy.data.collections:
                collection.user_clear()
                bpy.data.collections.remove(collection)        
            for material in bpy.data.materials:
                material.user_clear()
                bpy.data.materials.remove(material)
        oRgm = Chunky()
        if not oRgm.loadFromFile(self.sModelPath):
            print("Unable to load file")
        else:
            RgmIntoBlender(self, oRgm)
            pass
        
        return

'''
class ImportRgmPreferences(AddonPreferences):
    bl_idname = __name__
    
    path_to_rgm_folder : StringProperty( # type: ignore
        name = "Path to rgm Folder",
        description = "Path where there rgm file is",
        subtype='FILE_PATH',
        default = "",
    )
    
    def draw(self, context):
        layout = self.layout
        layout.label(text="Preferences:")
        layout.prop(self, "path_to_rgm_folder")

    @classmethod
    def get_path_to_rgm_folder(cls):
        return Path(bpy.context.preferences.addons[__name__].preferences.path_to_rgm_folder)

'''

class ImportRgmAddon(Operator, ImportHelper):
    """Import Rgm Importer"""
    bl_idname = "import.rgm_importer"
    bl_label = "Relic (.rgm)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = '.rgm'

    filter_glob: StringProperty(
        default = "*.rgm",
        options = {'HIDDEN'},
        maxlen = 255,
    )
    
    resetScene: BoolProperty(
        name = "Reset Scene",
        description = "Reset the blender scene",
        default = False,
    )
    
    filepath: StringProperty(
        name = "Import Model", 
        description = "File path of .rgm model file", 
        maxlen = 1024)
        
    importTextures: BoolProperty(
        name = "Import Textures",
        description = "Import .rgt/.dds texture files",
        default = False,
        )
    
    importAnimations: BoolProperty(
        name = "Import Animations",
        description = "Import the .rga animation file",
        default = False,
    )
        
    importDirectory : EnumProperty(
        items = [('Asset', "asset directory", "Import files from asset directory"), ('Work', "working directory", "Import files from working directory")],
        name = "Import from", 
        description = "Import directory of asset files",
        default = 'Work',
    )

    def execute(self, context):   
        #preferences = context.preferences
        #addon_prefs = preferences.addons[__name__].preferences
        importer = ImportRgm()
        importer.setData(self.resetScene, self.filepath, self.importTextures, self.importAnimations, self.importDirectory)
        importer.loadRgm()
        return {'FINISHED'}
    
    def invoke(self, context, event):
        self.filepath = "C:/Users/Carsten/Desktop/coh/CoH2/data/art/armies/german/vehicles/ostwind_flak_panzer"#"C:/Users/Carsten/Desktop/coh/antitank_75mm_pak40/" #hier from preference
        wm = context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

def menu_func_import(self, context):
    self.layout.operator(ImportRgmAddon.bl_idname, text=ImportRgmAddon.bl_label)

def register():
    bpy.utils.register_class(ImportRgmAddon)
    #bpy.utils.register_class(ImportRgmPreferences)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    bpy.utils.unregister_class(ImportRgmAddon)
    #bpy.utils.register_class(ImportRgmPreferences)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)

if __name__ == "__main__":
    register()



