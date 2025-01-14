import os
import sys
import random

import numpy as np
from pytorchtools import EarlyStopping
import torch.nn.functional as F

import torch
from torch.utils.data import DataLoader

from labelMaskDomain import labelMaskDomain
from dataset import dump_load, dump_write, NUS
from division2DifferenceTimeText.encoderMetadata import encoderMetadata
from division2DifferenceTimeText.evidence_ranker import evidenceRanker
from division2DifferenceTimeText.instanceEncoder import instanceEncoder
from division2DifferenceTimeText.labelEmbeddingLayer import labelEmbeddingLayer
from division2DifferenceTimeText.OneHotEncoder import oneHotEncoder
from torch import nn
from sklearn.metrics import f1_score
from transformers import AdamW
from transformers.optimization import get_linear_schedule_with_warmup

from transformers import BertTokenizer, AutoModel, AutoTokenizer

'''
Global version of extension 2: adding buckets of difference between time entities in text and claimdatum
With DistilRoBERTa encoder
'''
class verifactionModel(nn.Module):
    # Create neural network
    def __init__(self,transformer,metadataEncoder,instanceEncoder,evidenceRanker,labelEmbedding,labelMaskDomain,labelDomains,domain,alpha,withPretext=False):
        super(verifactionModel, self).__init__()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.tokenizer = AutoTokenizer.from_pretrained('sentence-transformers/all-distilroberta-v1')
        self.transformer = transformer
        self.metaDataEncoder = metadataEncoder
        self.instanceEncoder = instanceEncoder
        self.evidenceRanker = evidenceRanker
        self.labelEmbedding = labelEmbedding
        self.labelDomains = labelDomains
        self.labelMaskDomain = labelMaskDomain
        self.softmax = torch.nn.Softmax(dim=0).to(self.device)
        self.domain = domain
        self.alpha = float(alpha)
        self.claimDate = nn.Embedding(2, 768).to(self.device)
        self.evidenceDate = nn.Embedding(22, 768).to(self.device)
        self.positionEmbeddings = nn.Embedding(200, 768).to(self.device)
        self.predicateEmbeddings = nn.Embedding(2, 768).to(self.device)
        self.verschil = nn.Embedding(86, 768).to(self.device)
        self.wordEmbeddings = self.transformer.embeddings.word_embeddings.requires_grad_(True)
        self.withPretext = withPretext

    # Mean Pooling - Take attention mask into account for correct averaging
    def mean_pooling(self, model_output, attention_mask):
        token_embeddings = model_output[0]  # First element of model_output contains all token embeddings
        input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
        return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(input_mask_expanded.sum(1),min=1e-9)

    def forward(self,claim,evidences,metadata_encoding,domain,claimDate,snippetDates,verbsClaim,timeExpressionsClaim,positionClaim,timeRefsClaim,
                timeHeidelClaim,verbsSnippets,timeExpressionsSnippets,positionSnippets,timeRefsSnippets,timeHeidelSnippets,sizePreTextClaim,
                sizePretextSnippets  ):
        if self.withPretext:
            sizePreTextClaim = 0
        encoded_input = self.tokenizer(claim, padding=True, truncation=True, return_tensors='pt', max_length=512).to(self.device)
        model_output = self.transformer(**encoded_input)
        # Perform pooling
        claim_encoding = self.mean_pooling(model_output, encoded_input['attention_mask']).to(self.device)
        positions = positionClaim.split('\t')
        verbs = verbsClaim.split('\t')
        times = timeExpressionsClaim.split('\t')
        verschillenIndices = timeRefsClaim.split('\t')
        verschillenValues = timeHeidelClaim.split('\t')
        tijdAbsolute = torch.zeros(768).to(self.device)
        number = 0
        if verschillenIndices[0] != "":
            for i in range(len(verschillenIndices)):
                if verschillenIndices[i] >= sizePreTextClaim and verschillenValues[i].find('Duur') == -1 and verschillenValues[i].find('Refs') == -1:
                    if verschillenValues[i].isdigit():
                        tijdAbsolute = self.verschil(torch.tensor([int(verschillenValues[i])]).squeeze(0).to(self.device))
                        number += 1
                if i + 1 >= len(verschillenValues):
                    break
        if number > 0:
            claim_encoding = self.alpha*claim_encoding + (1-self.alpha)*tijdAbsolute/number
        distribution = torch.zeros(len(self.labelDomains[domain])).to(self.device)
        evidences = evidences.split(' 0123456789 ')[:-1]
        verbsSnippets = verbsSnippets.split(' 0123456789 ')[:-1]
        timeExpressionsSnippets = timeExpressionsSnippets.split(' 0123456789 ')[:-1]
        positionSnippets = positionSnippets.split(' 0123456789 ')[:-1]
        timeRefsSnippets = timeRefsSnippets.split(' 0123456789 ')[:-1]
        timeHeidelSnippets = timeHeidelSnippets.split(' 0123456789 ')[:-1]
        sizePretextSnippet = sizePretextSnippets.split(' 0123456789 ')[:-1]
        snippetDates = snippetDates.split('\t')
        for i in range(len(evidences)):
            encoded_input = self.tokenizer(evidences[i], padding=True, truncation=True, return_tensors='pt',
                                           max_length=512).to(self.device)
            model_output = self.transformer(**encoded_input)
            evidence_encoding = self.mean_pooling(model_output, encoded_input['attention_mask']).to(self.device)
            positionSnippet = positionSnippets[i].split('\t')
            verbsSnippet = verbsSnippets[i].split('\t')
            timeExpressionsSnippet = timeExpressionsSnippets[i].split('\t')
            timeRefsSnippet = timeRefsSnippets[i].split('\t')
            timeHeidelSnippet = timeHeidelSnippets[i].split('\t')
            tijdAbsolute = torch.zeros(768).to(self.device)
            number = 0
            if self.withPretext:
                sizePretextSnippet[i]=0
            if timeRefsSnippet[0] != "":
                for j in range(len(timeRefsSnippet)):
                    index = timeRefsSnippet[j]
                    if int(index) >= sizePretextSnippet[i] and timeHeidelSnippet[j].find('Duur') == -1 and timeHeidelSnippet[j].find('Refs') == -1:
                        if timeHeidelSnippet[j].isdigit():
                            if int(index)<512:
                                tijdAbsolute += self.verschil(
                                    torch.tensor([int(timeHeidelSnippet[j])]).to(self.device)).squeeze(
                                    0).to(self.device)
                                number += 1
                    if j + 1 >= len(timeHeidelSnippet):
                        break
            if number > 0:
                evidence_encoding = self.alpha*evidence_encoding+(1-self.alpha)*tijdAbsolute/number
            instance_encoding = self.instanceEncoder(claim_encoding.squeeze(0),evidence_encoding.squeeze(0),metadata_encoding.squeeze(0)).to(self.device)
            rank_evidence = self.evidenceRanker(instance_encoding).to(self.device)
            label_distribution = self.labelEmbedding(instance_encoding,domain).to(self.device)
            label_distributionDomain = self.labelMaskDomain(label_distribution).to(self.device)
            distribution = distribution + torch.mul(rank_evidence,label_distributionDomain).to(self.device)
        return distribution.to(self.device)

    def getRankingEvidencesLabels(self, claim, evidences, metadata_encoding, domain, claimDate, snippetDates,
                                  verbsClaim, timeExpressionsClaim, positionClaim, timeRefsClaim,
                                  timeHeidelClaim, verbsSnippets, timeExpressionsSnippets, positionSnippets,
                                  timeRefsSnippets, timeHeidelSnippets,sizePreTextClaim,
                                  sizePretextSnippets):
        if self.withPretext:
            sizePreTextClaim=0
        ranking = []
        labelsAll = []
        labelsDomain = []
        lastInstance_encoding = "None"
        allEqual = True
        encoded_input = self.tokenizer(claim, padding=True, truncation=True, return_tensors='pt', max_length=512).to(
            self.device)
        model_output = self.transformer(**encoded_input)
        # Perform pooling
        claim_encoding = self.mean_pooling(model_output, encoded_input['attention_mask']).to(self.device)
        verschillenIndices = timeRefsClaim.split('\t')
        verschillenValues = timeHeidelClaim.split('\t')
        tijdAbsolute = torch.zeros(768).to(self.device)
        number = 0
        if verschillenIndices[0] != "":
            for i in range(len(verschillenIndices)):
                if verschillenIndices[i] >= sizePreTextClaim and verschillenValues[i].find('Duur') == -1 and verschillenValues[i].find('Refs') == -1:
                    if verschillenValues[i].isdigit():
                        tijdAbsolute += self.verschil(
                            torch.tensor([int(verschillenValues[i])]).squeeze(0).to(self.device))
                        number += 1
                if i + 1 >= len(verschillenValues):
                    break
        if number > 0:
            claim_encoding = self.alpha * claim_encoding + (1 - self.alpha) * tijdAbsolute / number
        evidences = evidences.split(' 0123456789 ')[:-1]
        verbsSnippets = verbsSnippets.split(' 0123456789 ')[:-1]
        timeExpressionsSnippets = timeExpressionsSnippets.split(' 0123456789 ')[:-1]
        positionSnippets = positionSnippets.split(' 0123456789 ')[:-1]
        timeRefsSnippets = timeRefsSnippets.split(' 0123456789 ')[:-1]
        timeHeidelSnippets = timeHeidelSnippets.split(' 0123456789 ')[:-1]
        sizePretextSnippet = sizePretextSnippets.split(' 0123456789 ')[:-1]
        for i in range(len(evidences)):
            encoded_input = self.tokenizer(evidences[i], padding=True, truncation=True, return_tensors='pt',
                                           max_length=512).to(self.device)
            model_output = self.transformer(**encoded_input)
            evidence_encoding = self.mean_pooling(model_output, encoded_input['attention_mask']).to(self.device)
            # print(positionSnippets)
            timeRefsSnippet = timeRefsSnippets[i].split('\t')
            timeHeidelSnippet = timeHeidelSnippets[i].split('\t')
            tijdAbsolute = torch.zeros(768).to(self.device)
            number = 0
            if self.withPretext:
                sizePretextSnippet[i]=0
            if timeRefsSnippet[0] != "":
                for j in range(len(timeRefsSnippet)):
                    index = timeRefsSnippet[j]
                    if int(index) >= sizePretextSnippet[i] and timeHeidelSnippet[j].find('Duur') == -1 and timeHeidelSnippet[j].find('Refs') == -1:
                        if timeHeidelSnippet[j].isdigit():
                            if int(index) < 512:
                                tijdAbsolute += self.verschil(
                                    torch.tensor([int(timeHeidelSnippet[j])]).to(self.device)).squeeze(
                                    0).to(self.device)
                                number += 1
                    if j + 1 >= len(timeHeidelSnippet):
                        break
            if number > 0:
                evidence_encoding = self.alpha * claim_encoding + (1 - self.alpha) * tijdAbsolute / number
            instance_encoding = self.instanceEncoder(claim_encoding.squeeze(0), evidence_encoding.squeeze(0),
                                                     metadata_encoding.squeeze(0)).to(self.device)
            if allEqual:
                if lastInstance_encoding == "None":
                    lastInstance_encoding = instance_encoding
                else:
                    allEqual = torch.equal(lastInstance_encoding, instance_encoding)
                    lastInstance_encoding = instance_encoding
            rank_evidence = self.evidenceRanker(instance_encoding).to(self.device)
            ranking.append(rank_evidence.cpu().detach().item())
            label_distribution = self.labelEmbedding(instance_encoding, domain).to(self.device)
            labelsAll.append(label_distribution.cpu().detach().tolist())
            label_distributionDomain = self.labelMaskDomain(label_distribution).to(self.device)
            labelsDomain.append(label_distributionDomain.cpu().detach().tolist())
        return ranking, labelsAll, labelsDomain, allEqual

    def getRankingLabelsPerBin(self, index, claim, evidences, metadata_encoding, domain, claimDate, snippetDates,
                               verbsClaim, timeExpressionsClaim, positionClaim, timeRefsClaim,
                               timeHeidelClaim, verbsSnippets, timeExpressionsSnippets, positionSnippets,
                               timeRefsSnippets, timeHeidelSnippets,sizePreTextClaim,
                               sizePretextSnippets):
        if self.withPretext:
            sizePreTextClaim=0
        labelsAll = {}
        labelsAllIndices = {}
        labelsDomain = {}
        labelsDomainIndices = {}
        encoded_input = self.tokenizer(claim, padding=True, truncation=True, return_tensors='pt', max_length=512).to(
            self.device)
        model_output = self.transformer(**encoded_input)
        # Perform pooling
        claim_encoding = self.mean_pooling(model_output, encoded_input['attention_mask']).to(self.device)
        verschillenIndices = timeRefsClaim.split('\t')
        verschillenValues = timeHeidelClaim.split('\t')
        tijdAbsolute = torch.zeros(768).to(self.device)
        number = 0
        if verschillenIndices[0] != "":
            for i in range(len(verschillenIndices)):
                if verschillenIndices[i] >= sizePreTextClaim and verschillenValues[i].find('Duur') == -1 and verschillenValues[i].find('Refs') == -1:
                    if verschillenValues[i].isdigit():
                        tijdAbsolute += self.verschil(
                            torch.tensor([int(verschillenValues[i])]).squeeze(0).to(self.device))
                        number += 1
                if i + 1 >= len(verschillenValues):
                    break
        if number > 0:
            claim_encoding = self.alpha * claim_encoding + (1 - self.alpha) * tijdAbsolute / number
        evidences = evidences.split(' 0123456789 ')[:-1]
        verbsSnippets = verbsSnippets.split(' 0123456789 ')[:-1]
        timeExpressionsSnippets = timeExpressionsSnippets.split(' 0123456789 ')[:-1]
        positionSnippets = positionSnippets.split(' 0123456789 ')[:-1]
        timeRefsSnippets = timeRefsSnippets.split(' 0123456789 ')[:-1]
        timeHeidelSnippets = timeHeidelSnippets.split(' 0123456789 ')[:-1]
        sizePretextSnippet = sizePretextSnippets.split(' 0123456789 ')[:-1]
        snippetDates = snippetDates.split('\t')
        for i in range(len(evidences)):
            encoded_input = self.tokenizer(evidences[i], padding=True, truncation=True, return_tensors='pt',
                                           max_length=512).to(self.device)
            model_output = self.transformer(**encoded_input)
            evidence_encoding = self.mean_pooling(model_output, encoded_input['attention_mask']).to(self.device)
            timeRefsSnippet = timeRefsSnippets[i].split('\t')
            timeHeidelSnippet = timeHeidelSnippets[i].split('\t')
            tijdAbsolute = torch.zeros(768).to(self.device)
            number = 0
            if self.withPretext:
                sizePretextSnippet[i]=0
            if timeRefsSnippet[0] != "":
                for j in range(len(timeRefsSnippet)):
                    indexS = timeRefsSnippet[j]
                    if int(indexS) >= sizePretextSnippet[i] and timeHeidelSnippet[j].find('Duur') == -1 and timeHeidelSnippet[j].find('Refs') == -1:
                        if timeHeidelSnippet[j].isdigit():
                            if int(indexS) < 512:
                                tijdAbsolute += self.verschil(
                                    torch.tensor([int(timeHeidelSnippet[j])]).to(self.device)).squeeze(
                                    0).to(self.device)
                                number += 1
                    if j + 1 >= len(timeHeidelSnippet):
                        break
            if number > 0:
                evidence_encoding = self.alpha * evidence_encoding + (1 - self.alpha) * tijdAbsolute / number
            instance_encoding = self.instanceEncoder(claim_encoding.squeeze(0), evidence_encoding.squeeze(0),
                                                     metadata_encoding.squeeze(0)).to(self.device)
            timeSnippetsEntry = set()
            for i in range(len(timeHeidelSnippet)):
                if timeHeidelSnippet[i].find('Duur') == -1 and timeHeidelSnippet[i].find('Refs') == -1:
                    if timeHeidelSnippet[i].isdigit():
                        timeSnippetsEntry.add(timeHeidelSnippet[i])

            label_distribution = self.labelEmbedding(instance_encoding, domain).to(self.device)
            ranking = label_distribution.cpu().detach().tolist()
            labelsRanking = [sorted(ranking, reverse=True).index(x) + 1 for x in ranking]
            for date in timeSnippetsEntry:
                if date in labelsAll:
                    labelsAll[date].append(labelsRanking)
                    labelsAllIndices[date].append(index + "-" + str(i))
                else:
                    labelsAll[date] = [labelsRanking]
                    labelsAllIndices[date] = [index + "-" + str(i)]
            label_distributionDomain = self.labelMaskDomain(label_distribution).to(self.device)
            rankingDomain = label_distributionDomain.cpu().detach().tolist()
            labelsRankingDomain = [sorted(rankingDomain, reverse=True).index(x) + 1 for x in rankingDomain]
            for date in timeSnippetsEntry:
                if date in labelsDomain:
                    labelsDomain[date].append(labelsRankingDomain)
                    labelsDomainIndices[date].append(index + "-" + str(i))
                else:
                    labelsDomain[date] = [labelsRankingDomain]
                    labelsDomainIndices[date] = [index + "-" + str(i)]
        return labelsDomain, labelsAll, labelsDomainIndices, labelsAllIndices
    '''
        Function for saving the neural network
    '''
    def saving_NeuralNetwork(model,path):
        pathI = path +"/"+model.domain
        if not os.path.exists(pathI):
            os.mkdir(pathI)
        torch.save(model.state_dict(), pathI + '/model')

    '''
    Function for loading the neural network
    '''
    def loading_NeuralNetwork(model,path):
        pathI = path +"/"+model.domain
        model.load_state_dict(torch.load(pathI + '/model', map_location=torch.device('cpu')))
        model.eval()
        return model

def eval_loop(dataloader, model,oneHotEncoder,domainLabels,domainLabelIndices,device):
    groundTruthLabels = []
    predictedLabels = []
    loss = nn.CrossEntropyLoss(reduction="sum")
    totalLoss = 0
    with torch.no_grad():
        for c in dataloader:
            # Compute prediction and loss
            # outcome of feedforwarding feature vector to image neural network
            # set gradient to true
            predictionBatch = torch.tensor([])
            for i in range(len(c[0])):
                metaDataClaim = oneHotEncoder.encode(c[3][i],device)
                metadata_encoding = model.metaDataEncoder(metaDataClaim.unsqueeze(0)).to(device)
                domain = c[0][0].split('-')[0]
                #print(c[0][i])
                prediction = model(c[1][i], c[2][i], metadata_encoding, domain, c[5][i], c[6][i],
                                   c[7][i],
                                   c[8][i], c[9][i], c[10][i], c[11][i], c[12][i], c[13][i],
                                   c[14][i], c[15][i], c[16][i],c[17][i],c[18][i]).to(device)
                if predictionBatch.size()[0]==0:
                    predictionBatch = prediction.unsqueeze(0).to(device)
                    predictedLabels.append(torch.argmax(prediction).item())
                else:
                    predictionBatch = torch.cat((predictionBatch,prediction.unsqueeze(0))).to(device)
                    predictedLabels.append(torch.argmax(prediction).item())

            labelIndices = []
            for label in c[4]:
                labelIndices.append(domainLabelIndices[domain][domainLabels[domain].index(label)])
                groundTruthLabels.append(domainLabelIndices[domain][domainLabels[domain].index(label)])
            target = torch.tensor(labelIndices).to(device)
            output = loss(predictionBatch,target)
            totalLoss += output.item()


    macro = f1_score(groundTruthLabels, predictedLabels, average='macro')
    micro = f1_score(groundTruthLabels, predictedLabels, average='micro')

    return totalLoss,micro,macro

def getLabelIndicesDomain(domainPath,labelPath):
    domainsIndices = dict()
    domainsLabels = dict()
    domainLabelIndices = dict()
    labelSequence = []
    file = open(labelPath,'r')
    lines = file.readlines()
    for line in lines:
        labelSequence.append(line.replace('\n',''))
    file = open(domainPath,'r')
    lines = file.readlines()
    for line in lines:
        parts = line.split("\t")
        labelsDomain = parts[1].split(",")
        labelsDomain[-1] = labelsDomain[-1].replace('\n','')
        labelIndices = []
        for label in labelsDomain:
            labelIndices.append(labelSequence.index(label.replace('\n','')))
        labelIndicesDomainM = sorted(labelIndices)
        labelIndicesDomain = []
        for index in labelIndices:
            labelIndicesDomain.append(labelIndicesDomainM.index(index))
        domainsIndices[parts[0]] = labelIndices
        domainsLabels[parts[0]] = labelsDomain
        domainLabelIndices[parts[0]] = labelIndicesDomain

    return domainsIndices,domainsLabels,domainLabelIndices

def calculatePrecisionDev(dataloader, model,oneHotEncoder,domainLabels,domainLabelIndices,device):
    groundTruthLabels = []
    predictedLabels = []
    with torch.no_grad():
        for batch in dataloader:
            for i in range(len(batch[0])):
                metaDataClaim = oneHotEncoder.encode(batch[3][i],device)
                metadata_encoding = model.metaDataEncoder(metaDataClaim.unsqueeze(0)).to(device)
                domain = batch[0][0].split('-')[0]
                prediction = model(batch[1][i], batch[2][i], metadata_encoding, domain, batch[5][i], batch[6][i],
                                   batch[7][i],
                                   batch[8][i], batch[9][i], batch[10][i], batch[11][i], batch[12][i], batch[13][i],
                                   batch[14][i], batch[15][i], batch[16][i],batch[17][i],batch[18][i]).to(device)
                groundTruthLabels.append(domainLabelIndices[domain][domainLabels[domain].index(batch[4][i])])
                predictedLabels.append(torch.argmax(prediction).item())

    print('Micro F1 - score')
    print(f1_score(groundTruthLabels, predictedLabels, average='micro'))
    print('Macro F1-score')
    print(f1_score(groundTruthLabels, predictedLabels, average='macro'))

def getPredictions(dataloader, model,oneHotEncoder,domainLabels,domainLabelIndices,device):
    predictions = dict()
    with torch.no_grad():
        for batch in dataloader:
            for i in range(len(batch[0])):
                metaDataClaim = oneHotEncoder.encode(batch[3][i],device)
                metadata_encoding = model.metaDataEncoder(metaDataClaim.unsqueeze(0)).to(device)
                domain = batch[0][0].split('-')[0]
                prediction = model(batch[1][i], batch[2][i], metadata_encoding, domain, batch[5][i], batch[6][i],
                                   batch[7][i],
                                   batch[8][i], batch[9][i], batch[10][i], batch[11][i], batch[12][i], batch[13][i],
                                   batch[14][i], batch[15][i], batch[16][i],batch[17][i],batch[18][i]).to(device)
                pIndex = torch.argmax(prediction).item()
                plabel = domainLabels[domain][domainLabelIndices[domain].index(pIndex)]
                predictions[batch[0][i]] = plabel
    return predictions

def train(batch,model,oneHotEncoder, optimizer,domainLabels,domainLabelIndices,device,preprocessing = False):
    model.train()
    loss = nn.CrossEntropyLoss(reduction="sum")
    predictionBatch = torch.tensor([],requires_grad=True).to(device)
    for i in range(len(batch[0])):
        metaDataClaim = oneHotEncoder.encode(batch[3][i],device).to(device)
        metadata_encoding = model.metaDataEncoder(metaDataClaim.unsqueeze(0)).to(device)
        domain = batch[0][0].split('-')[0]
        prediction = model(batch[1][i], batch[2][i], metadata_encoding, domain,batch[5][i],batch[6][i],batch[7][i],
                           batch[8][i],batch[9][i],batch[10][i],batch[11][i],batch[12][i],batch[13][i],
                           batch[14][i],batch[15][i],batch[16][i],batch[17][i],batch[18][i]).to(device)
        if predictionBatch.size()[0] == 0:
            predictionBatch = prediction.unsqueeze(0).to(device)
        else:
            predictionBatch = torch.cat((predictionBatch, prediction.unsqueeze(0))).to(device)
    labelIndices = []
    for label in batch[4]:
        labelIndices.append(domainLabelIndices[domain][domainLabels[domain].index(label)])
    target = torch.tensor(labelIndices).to(device)
    output = loss(predictionBatch, target)
    # Backpropagation
    # reset the gradients of model parameters
    optimizer.zero_grad()
    # calculate gradients of the loss w.r.t. each parameter
    output.backward()
    #  adjust the parameters by the gradients collected in the backward pass
    optimizer.step()
    return output.item()


def writePredictions(predictions,file,output):
    with open(file,'r',encoding='utf-8') as file:
        with open(output,'w',encoding='utf-8') as output:
            for line in file:
                elements = line.split('\t')
                output.write(predictions[elements[0]])
                output.write('\n')
            output.close()

def writeMetadata(metadataSet):
    with open('../Metadata_sequence/metadata', 'w', encoding='utf-8') as file:
        for metadata in metadataSet:
            file.write(metadata)
            file.write('\n')
        file.close()

def readMetadata():
    metadataSequence = []
    with open('../Metadata_sequence/metadata', 'r', encoding='utf-8') as file:
        for metadata in file:
            metadataSequence.append(metadata)
        file.close()
    return metadataSequence

def preprocessing(models,epochs=800):
    with torch.no_grad():
        for model in models:
            model[1].eval()
            validation_loss, microF1Test, macroF1Test = eval_loop(model[9], model[1], oneHotEncoderM, domainLabels,
                                                          domainLabelIndices, device)
            model[8](validation_loss, microF1Test, macroF1Test, model[1])
    print('Start preprocessing')
    for i in range(epochs):
        print('Epoch ' + str(i))
        newModels = list()
        for model in models:
            batch = next(model[0])
            loss = train(batch,model[1],oneHotEncoderM,model[2],domainLabels,domainLabelIndices,device,True)
            model[10].step()
        with torch.no_grad():
            for model in models:
                it = i
                model[1].eval()
                newModels.append(
                    tuple(
                        [iter(model[7]), model[1], model[2], model[3], model[4], model[5], it, model[7], model[8],
                         model[9],
                         model[10]]))
                validation_loss, microF1Test, macroF1Test = eval_loop(model[9], model[1], oneHotEncoderM, domainLabels,
                                                                      domainLabelIndices, device)
                model[8](validation_loss, microF1Test, macroF1Test, model[1])
        models = newModels
        random.shuffle(models)

if __name__ == "__main__":
    '''
        argument 1 path to save the model/where previous model is saved
                 2 parameter alpha
                 3 evaluation/training mode
                 4 withPreText
    '''
    torch.manual_seed(1)
    random.seed(1)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    domainIndices, domainLabels, domainLabelIndices = getLabelIndicesDomain(
        'labels/labels.tsv','labels/labelSequence')
    oneHotEncoderM = oneHotEncoder('Metadata_sequence/metadata')
    domains = domainIndices.keys()
    metadataSet = set()
    labelEmbeddingLayerM = labelEmbeddingLayer(2308, domainIndices)
    domainModels = []
    losses = np.zeros(len(domains))
    index = 0
    transformer = AutoModel.from_pretrained('sentence-transformers/all-distilroberta-v1').to(device)
    encoderMetadataM = encoderMetadata(3, 3, oneHotEncoderM).to(device)
    instanceEncoderM = instanceEncoder().to(device)
    evidenceRankerM = evidenceRanker(2308, 100).to(device)
    for domain in domains:
        train_set = NUS(mode='Train', path='train/train-' + domain + '.tsv', domain=domain)
        dev_set = NUS(mode='Dev', path='dev/dev-' + domain + '.tsv', domain=domain)
        test_set = NUS(mode='Test', path='test/test-' + domain + '.tsv', domain=domain)
        trainMetadata = train_set.getMetaDataSet()
        devMetadata = dev_set.getMetaDataSet()
        testMetadata = test_set.getMetaDataSet()
        metadataSet = set.union(metadataSet, trainMetadata)
        metadataSet = set.union(metadataSet, devMetadata)
        metadataSet = set.union(metadataSet, testMetadata)
        train_loader = DataLoader(train_set,
                                  batch_size=32,
                                  shuffle=True)
        dev_loader = DataLoader(dev_set,
                                batch_size=32,
                                shuffle=True)
        test_loader = DataLoader(test_set,
                                 batch_size=32,
                                 shuffle=True)
        labelMaskDomainM = labelMaskDomain(2308, domainIndices, domain, len(domainIndices[domain])).to(device)
        verificationModelM = verifactionModel(transformer, encoderMetadataM, instanceEncoderM,
                                              evidenceRankerM,
                                              labelEmbeddingLayerM, labelMaskDomainM, domainIndices,
                                              domain,sys.argv[2],bool(sys.argv[4])).to(device)
        domainModel = [train_loader, dev_loader, test_loader, verificationModelM, domain, index]
        domainModels.append(domainModel)
        index += 1

    if sys.argv[3] == "evaluation":
        microF1All = 0
        macroF1All = 0
        for model in domainModels:
            NNmodel = model[3].loading_NeuralNetwork(sys.argv[1]).to(device)
            print("Results model na epochs " + model[4])
            calculatePrecisionDev(model[2], NNmodel, oneHotEncoderM, domainLabels, domainLabelIndices, device)
            validation_loss, microF1, macroF1 = eval_loop(model[2], NNmodel, oneHotEncoderM, domainLabels,
                                                          domainLabelIndices, device)
            print('Loss - ' + str(validation_loss))
            microF1All += microF1
            macroF1All += macroF1
        print('Average micro ')
        print(str(microF1All / 26))
        print('Average macro')
        print(str(macroF1All / 26))
    else:
        # number of epochs
        epochs = 300
        # This is the bestAcc we have till now
        bestAcc = 0

        models = set()
        with torch.no_grad():
            for model in domainModels:
                # early_stopping
                early_stopping = EarlyStopping(patience=3, verbose=True)
                NNmodel = model[3]
                NNmodel.eval()
                validation_loss, microF1, macroF1 = eval_loop(model[1], NNmodel, oneHotEncoderM, domainLabels,
                                                              domainLabelIndices, device)
                early_stopping(validation_loss, microF1, macroF1, NNmodel)
                optimizer = AdamW(NNmodel.parameters(), lr=5e-3)
                numberOfTrainingSteps = 6000
                scheduler = get_linear_schedule_with_warmup(
                    optimizer, num_warmup_steps=0,
                    num_training_steps=numberOfTrainingSteps
                )
                models.add(
                    tuple([iter(model[0]), model[3], optimizer, model[4],
                           model[5], model[2], 0, model[0], early_stopping,
                           model[1], scheduler]))

        preprocessing(models)

        models = set()

        with torch.no_grad():
            for model in domainModels:
                # early_stopping
                early_stopping = EarlyStopping(patience=3, verbose=True)
                NNmodel = model[3].loading_NeuralNetwork(sys.argv[1]).to(device)
                NNmodel.eval()
                print("Results model na preprocessing " + ' ' + model[5])
                calculatePrecisionDev(model[1], NNmodel, oneHotEncoderM, domainLabels, domainLabelIndices, device)
                validation_loss, microF1, macroF1 = eval_loop(model[1], NNmodel, oneHotEncoderM, domainLabels,
                                                              domainLabelIndices, device)
                early_stopping(validation_loss, microF1, macroF1, NNmodel)
                optimizer = torch.optim.AdamW(NNmodel.parameters(), lr=1e-4)
                numberOfTrainingSteps = len(model[0])
                if numberOfTrainingSteps % 32 == 0:
                    numberOfTrainingSteps = numberOfTrainingSteps / 32 * epochs * 100
                else:
                    numberOfTrainingSteps = (numberOfTrainingSteps // 32 + 1) * epochs * 100
                scheduler = get_linear_schedule_with_warmup(
                    optimizer, num_warmup_steps=0,
                    num_training_steps=numberOfTrainingSteps
                )
                models.add(
                    tuple([iter(model[0]), model[3], optimizer, model[4],
                           model[5], model[2], 0, model[0], early_stopping,
                           model[1], scheduler]))

        print('start finetuning')
        while models:
            removeModels = []
            removeEntirely = set()
            for model in models:
                batch = next(model[0], "None")
                try:
                    if batch != "None":
                        loss = train(batch, model[1], oneHotEncoderM, model[2], domainLabels, domainLabelIndices, device)
                        losses[model[4]] += loss
                        if model[10] != "none":
                            model[10].step()
                    else:
                        removeModels.append(model)
                except:
                    removeEntirely.add(model)
            if len(removeModels) != 0:
                for model in removeModels:
                    models.remove(model)
                    if model not in removeEntirely:
                        model[1].eval()
                        it = model[6] + 1
                        validation_loss, microF1Test, macroF1Test = eval_loop(model[9], model[1], oneHotEncoderM,
                                                                              domainLabels,
                                                                              domainLabelIndices, device)
                        model[8](validation_loss, microF1Test, macroF1Test, model[1])
                        if not model[8].early_stop and it < epochs:
                            models.add(tuple(
                                [iter(model[7]), model[1], model[2], model[3], model[4], model[5], it, model[7],
                                 model[8],
                                 model[9], model[10]]))
                        else:
                            print("Early stopping")
                        losses[model[4]] = 0
        precisionModels = dict()
        microF1All = 0
        macroF1All = 0
        for model in domainModels:
            NNmodel = model[3].loading_NeuralNetwork(sys.argv[1]).to(device)
            print("Results model na epochs " + model[4])
            calculatePrecisionDev(model[2], NNmodel, oneHotEncoderM, domainLabels, domainLabelIndices, device)
            validation_loss, microF1, macroF1 = eval_loop(model[2], NNmodel, oneHotEncoderM, domainLabels,
                                                          domainLabelIndices, device)
            print('Loss - ' + str(validation_loss))
            microF1All += microF1
            macroF1All += macroF1
        print('Average micro ')
        print(str(microF1All / 26))
        print('Average macro')
        print(str(macroF1All / 26))